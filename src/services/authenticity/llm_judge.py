"""Authenticity LLM judge.

Single public coroutine :func:`judge_authenticity`. It scores every
authenticity signal in one Claude (Sonnet) call and returns a
:class:`~src.schemas.authenticity_judge.JudgeResult` — per-signal
:class:`~src.schemas.authenticity.SignalResult` plus the judge's
one-paragraph rationale.

Two calibrations, chosen by whether a job is supplied:

* **baseline** (no job) — the five job-agnostic signals. Feeds the
  candidate-level baseline score (also used for pool-only CV ingests).
* **job-aware** (job title + required skills) — all six signals, adding
  JD Keyword Mirroring. Feeds the application-level score.

The call:

1. Truncates ``cv_text`` to :data:`MAX_CV_TEXT_CHARS` and JSON-serializes
   ``parsed_cv`` so Claude sees both the structured summary and the raw
   prose. Either alone is insufficient — the summary exposes internal
   consistency, the raw text is where achievement specificity lives.
2. Calls Claude via tool-use structured output bound to the calibration's
   response schema, using the base prompt version (7-day Redis cache in
   the wrapper).
3. On a bind failure retries **once** with a stricter system prompt (own
   cache slot via a distinct ``prompt_version``).
4. If the strict retry also fails, returns every signal at
   :data:`JUDGE_FAILURE_SCORE` (50.0) with a fallback finding and no
   rationale — 50 reads as "the judge couldn't decide" rather than
   inflating the aggregate toward authentic.

CV text and Claude responses are *never* logged — PII / confidential. Log
records carry only model + retried/failed flags + final scores.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any, Final

from pydantic import ValidationError

from src.ai.claude import (
    ClaudeStructuredOutputError,
    call_claude_structured,
    load_chat_prompt,
)
from src.enums.authenticity_signals import BASELINE_SIGNAL_KEYS, SignalComputedBy, SignalKey
from src.enums.claude_models import ClaudeModel
from src.enums.language import LanguageCode
from src.enums.prompts import (
    build_authenticity_system_prompt,
    build_authenticity_system_prompt_strict,
)
from src.enums.thresholds import AUTHENTICITY_CONCERN_THRESHOLD
from src.schemas.authenticity import SignalResult
from src.schemas.authenticity_judge import (
    JUDGE_FINDING_MAX_LENGTH,
    JUDGE_RATIONALE_MAX_LENGTH,
    JUDGE_SCORE_MAX,
    JUDGE_SCORE_MIN,
    AuthenticityBaselineResponse,
    AuthenticityJobAwareResponse,
    JudgeResult,
    JudgeSignalReport,
)
from src.schemas.parsed_cv import ParsedCV
from src.utils.language import detect_language

logger = logging.getLogger(__name__)

# --- Constants --------------------------------------------------------------

PROMPT_TEMPLATE: Final[str] = "authenticity_judge.j2"

# Distinct cache-key tags per calibration + attempt so no slot ever serves
# another's output. Bump the version segment when prompt semantics change —
# v3 added the LENGTH RULE (findings ≤2 sentences / 450 chars); v4 reworks the
# ``rationale`` into a recruiter-facing comment (neutral, no scores/signal
# names, non-accusatory, advisory) so stale synthesis-style prose isn't served;
# v5 tightens that comment to at most 2 sentences (2 lines) so cached 3-sentence
# rationales aren't re-served; v6 adds the PLAIN-LANGUAGE rule (ban internal
# analytical jargon in the comment, with rewrite examples) so cached jargon-laden
# rationales aren't re-served.
PROMPT_VERSION_BASELINE_BASE: Final[str] = "authenticity_judge:baseline:base:v6"
PROMPT_VERSION_BASELINE_STRICT: Final[str] = "authenticity_judge:baseline:strict:v6"
PROMPT_VERSION_JOBAWARE_BASE: Final[str] = "authenticity_judge:jobaware:base:v6"
PROMPT_VERSION_JOBAWARE_STRICT: Final[str] = "authenticity_judge:jobaware:strict:v6"

# Six signals, each a short finding + one synthesis paragraph. Generous
# headroom keeps cost predictable without clipping the reasoning chain.
MAX_TOKENS: Final[int] = 4096

# Hard cut on CV text (a hard clip, not a smart summary — Claude already
# saw the structured summary). ~20k chars ≈ 5k tokens.
MAX_CV_TEXT_CHARS: Final[int] = 20_000

# Score assigned to every signal when Claude fails twice. 50 (not 75) so a
# Claude outage lands dead-center rather than inflating toward authentic.
JUDGE_FAILURE_SCORE: Final[float] = 50.0


# Prompts built once per calibration/attempt: literal system turn + the Jinja
# human template. The base/strict pair carry different system prompts, matching
# their two cache slots.
_PROMPT_BASELINE_BASE: Final = load_chat_prompt(
    system=build_authenticity_system_prompt(job_aware=False), human_template_file=PROMPT_TEMPLATE
)
_PROMPT_BASELINE_STRICT: Final = load_chat_prompt(
    system=build_authenticity_system_prompt_strict(job_aware=False),
    human_template_file=PROMPT_TEMPLATE,
)
_PROMPT_JOBAWARE_BASE: Final = load_chat_prompt(
    system=build_authenticity_system_prompt(job_aware=True), human_template_file=PROMPT_TEMPLATE
)
_PROMPT_JOBAWARE_STRICT: Final = load_chat_prompt(
    system=build_authenticity_system_prompt_strict(job_aware=True),
    human_template_file=PROMPT_TEMPLATE,
)


def _truncate(text: str, limit: int) -> str:
    return text[:limit] if len(text) > limit else text


def _parsed_cv_summary_json(parsed_cv: ParsedCV) -> str:
    """JSON-serialize the parsed CV in a stable form for the prompt."""
    payload = parsed_cv.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2)


def _language_hint(cv_text: str) -> str:
    """A short in-prompt note for non-English CVs, or '' for English."""
    language = detect_language(cv_text)
    if language is LanguageCode.AR:
        return (
            "Note: this CV is in Arabic. Apply the same signals — Arabic "
            "AI-generated CVs show the same tells as English ones."
        )
    if language is not LanguageCode.EN:
        return "Note: this CV mixes languages. Assess all sections using the same signals."
    return ""


def _to_signal_result(report: JudgeSignalReport) -> SignalResult:
    """Convert one wire-format report into a service-grade SignalResult.

    Schema constraints already guarantee ``0 <= score <= 100`` and a
    non-empty finding. ``concern`` is derived here (never trusted from the
    model) so it can't drift from the score.
    """
    concern = report.score < AUTHENTICITY_CONCERN_THRESHOLD
    return SignalResult(
        score=float(report.score),
        computed_by=SignalComputedBy.CLAUDE,
        finding=report.finding,
        concern=concern,
        reasons=[report.finding],
        details={"score": report.score},
    )


def _failure_result(signal_keys: Sequence[SignalKey], reason: str) -> JudgeResult:
    """Both-attempts-failed fallback: every signal at :data:`JUDGE_FAILURE_SCORE`."""
    fallback = SignalResult(
        score=JUDGE_FAILURE_SCORE,
        computed_by=SignalComputedBy.CLAUDE,
        finding=None,
        concern=JUDGE_FAILURE_SCORE < AUTHENTICITY_CONCERN_THRESHOLD,
        reasons=[
            "Claude authenticity judge failed twice; "
            f"defaulting to {JUDGE_FAILURE_SCORE:.0f}. Reason: {reason}"
        ],
        details={"judge_failed": True, "reason": reason},
    )
    return JudgeResult(signals={key: fallback for key in signal_keys}, rationale=None)


def _build_result(
    validated: AuthenticityBaselineResponse,
    *,
    retried: bool,
    truncated: bool,
    job_aware: bool,
) -> JudgeResult:
    """Log the PII-safe success line and convert the wire format to the result."""
    reports = validated.signals()
    signals = {key: _to_signal_result(report) for key, report in reports.items()}
    logger.info(
        "llm_judge.scored",
        extra={
            "model": ClaudeModel.SONNET.value,
            "job_aware": job_aware,
            "retried": retried,
            "input_truncated": truncated,
            "signal_scores": {key.value: report.score for key, report in reports.items()},
        },
    )
    return JudgeResult(signals=signals, rationale=validated.rationale)


async def _call_judge(
    *,
    variables: dict[str, str],
    prompt: Any,
    prompt_version: str,
    schema: type[AuthenticityBaselineResponse],
) -> AuthenticityBaselineResponse:
    """Call Claude for the calibration's schema-bound response.

    Raises :class:`ClaudeStructuredOutputError` if the response can't be bound.
    """
    return await call_claude_structured(
        model=ClaudeModel.SONNET,
        prompt=prompt,
        variables=variables,
        schema=schema,
        max_tokens=MAX_TOKENS,
        prompt_version=prompt_version,
    )


def _salvage_from_raw_args(
    raw_args: dict[str, Any],
    schema: type[AuthenticityBaselineResponse],
) -> AuthenticityBaselineResponse | None:
    """Best-effort repair of a response that scored fine but failed to bind.

    The judge's most common bind failure is benign: a well-formed reply whose
    only fault is one ``finding`` (or the ``rationale``) running past its
    length cap — most often ``timeline_coherence`` on skill-dense CVs, where
    the model narrates every skill-vs-role gap. Rather than spend a second
    ~40s Sonnet call on the strict retry (or, on a double failure, discard the
    real scores for a meaningless all-:data:`JUDGE_FAILURE_SCORE` verdict), we
    truncate the over-long strings, coerce+clamp each score to an int, keep
    only the schema's own keys (so a stray field can't re-trip
    ``extra='forbid'``), and re-validate.

    Returns the bound model on success, or ``None`` when the output is too
    malformed to trust — a missing signal, a non-numeric score, or an empty
    finding — in which case the caller proceeds to the strict retry exactly as
    before. ``raw_args`` is empty when the model didn't call the tool at all;
    that also yields ``None``.
    """
    if not raw_args:
        return None

    repaired: dict[str, Any] = {}
    for name in schema.model_fields:
        if name == "rationale":
            rationale = raw_args.get("rationale")
            if not isinstance(rationale, str) or not rationale.strip():
                return None
            repaired["rationale"] = rationale[:JUDGE_RATIONALE_MAX_LENGTH]
            continue

        signal = raw_args.get(name)
        if not isinstance(signal, dict):
            return None
        finding = signal.get("finding")
        if not isinstance(finding, str) or not finding.strip():
            return None
        try:
            score = round(float(signal["score"]))
        except (KeyError, TypeError, ValueError):
            return None
        repaired[name] = {
            "score": max(JUDGE_SCORE_MIN, min(JUDGE_SCORE_MAX, score)),
            "finding": finding[:JUDGE_FINDING_MAX_LENGTH],
        }

    try:
        return schema.model_validate(repaired)
    except ValidationError:
        return None


async def judge_authenticity(
    cv_text: str,
    parsed_cv: ParsedCV,
    *,
    job_title: str | None = None,
    required_skills: Sequence[str] | None = None,
) -> JudgeResult:
    """Score every authenticity signal via one Claude call.

    Runs the **job-aware** (six-signal) calibration when ``job_title`` is
    given, else the **baseline** (five-signal) calibration. Never raises on
    Claude content failure — on a second-attempt failure every signal falls
    back to :data:`JUDGE_FAILURE_SCORE`.

    Args:
        cv_text: Raw CV text (truncated to :data:`MAX_CV_TEXT_CHARS`).
        parsed_cv: The structured parse, serialized to JSON for the prompt.
        job_title: The job's title. When present (with ``required_skills``),
            enables the JD Keyword Mirroring signal.
        required_skills: The job's required skills, for mirroring calibration.
    """
    job_aware = bool(job_title)
    signal_keys: tuple[SignalKey, ...] = tuple(SignalKey) if job_aware else BASELINE_SIGNAL_KEYS

    truncated_cv_text = _truncate(cv_text, MAX_CV_TEXT_CHARS)
    truncated = len(cv_text) > MAX_CV_TEXT_CHARS
    variables = {
        "cv_text": truncated_cv_text,
        "parsed_cv_json": _parsed_cv_summary_json(parsed_cv),
        "language_hint": _language_hint(cv_text),
        "job_title": job_title or "",
        "required_skills": ", ".join(required_skills) if required_skills else "",
    }

    if job_aware:
        schema: type[AuthenticityBaselineResponse] = AuthenticityJobAwareResponse
        base_prompt, strict_prompt = _PROMPT_JOBAWARE_BASE, _PROMPT_JOBAWARE_STRICT
        base_version, strict_version = PROMPT_VERSION_JOBAWARE_BASE, PROMPT_VERSION_JOBAWARE_STRICT
    else:
        schema = AuthenticityBaselineResponse
        base_prompt, strict_prompt = _PROMPT_BASELINE_BASE, _PROMPT_BASELINE_STRICT
        base_version, strict_version = PROMPT_VERSION_BASELINE_BASE, PROMPT_VERSION_BASELINE_STRICT

    # --- First attempt: base prompt -----------------------------------------
    try:
        validated = await _call_judge(
            variables=variables,
            prompt=base_prompt,
            prompt_version=base_version,
            schema=schema,
        )
    except ClaudeStructuredOutputError as exc:
        salvaged = _salvage_from_raw_args(exc.raw_args, schema)
        if salvaged is not None:
            logger.info(
                "llm_judge.salvaged",
                extra={
                    "model": ClaudeModel.SONNET.value,
                    "job_aware": job_aware,
                    "attempt": "base",
                },
            )
            return _build_result(salvaged, retried=False, truncated=truncated, job_aware=job_aware)
        first_failure_reason: str | None = "structured_bind_failed"
    else:
        return _build_result(validated, retried=False, truncated=truncated, job_aware=job_aware)

    logger.warning(
        "llm_judge.validation_failed_retrying",
        extra={"model": ClaudeModel.SONNET.value, "job_aware": job_aware},
    )

    # --- Second attempt: strict prompt --------------------------------------
    try:
        validated = await _call_judge(
            variables=variables,
            prompt=strict_prompt,
            prompt_version=strict_version,
            schema=schema,
        )
    except ClaudeStructuredOutputError as exc:
        salvaged = _salvage_from_raw_args(exc.raw_args, schema)
        if salvaged is not None:
            logger.info(
                "llm_judge.salvaged",
                extra={
                    "model": ClaudeModel.SONNET.value,
                    "job_aware": job_aware,
                    "attempt": "strict",
                },
            )
            return _build_result(salvaged, retried=True, truncated=truncated, job_aware=job_aware)
        second_failure_reason: str | None = "structured_bind_failed"
    else:
        return _build_result(validated, retried=True, truncated=truncated, job_aware=job_aware)

    logger.warning(
        "llm_judge.fallback_to_neutral",
        extra={
            "model": ClaudeModel.SONNET.value,
            "job_aware": job_aware,
            "fallback_score": JUDGE_FAILURE_SCORE,
        },
    )
    return _failure_result(signal_keys, second_failure_reason or first_failure_reason or "unknown")
