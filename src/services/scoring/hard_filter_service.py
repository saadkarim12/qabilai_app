"""Hard-filter scoring service (Step 4.3).

Single public coroutine :func:`score_hard_filters`. It

1. Renders the prompt (template :data:`PROMPT_TEMPLATE`) with the job
   spec, the parsed CV summary, and the raw CV text. Same dual-view
   approach as the authenticity judge: structured + raw together gives
   Opus the best context for the per-signal rubrics.
2. Calls Claude Opus via tool-use structured output bound to
   :class:`HardFilterClaudeResponse`, using the base prompt version. The
   Claude wrapper handles the 7-day Redis cache.
3. If the response can't be bound to the schema
   (:class:`~src.ai.claude.ClaudeStructuredOutputError`), retries **once**
   with a stricter system prompt (own cache slot via distinct
   ``prompt_version``).
4. If the strict retry also fails, raises
   :class:`HardFilterScoringError`. The worker maps that to
   ``pipeline_status['hard_filter'] = 'failed'`` so HR sees the failure
   in the detail endpoint.

Transient Anthropic errors (rate-limit, overloaded, connection) are
*not* caught here — they propagate out so the Celery worker's
``autoretry_for`` handles them. Only **content** failures (schema /
JSON parse) trigger the strict-retry path.

Weights live in :data:`HARD_FILTER_WEIGHTS` (:mod:`src.enums.hard_filters`)
— the architecture doc pins them at 35/35/10/10/10. Total is computed
in Python so the model's job is *only* scoring per-signal; arithmetic
is our responsibility.

CV text and Claude responses are *never* logged — they're PII /
confidential. Log records carry only model + retried/failed flags +
the final aggregate score.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final

from src.ai.claude import (
    ClaudeStructuredOutputError,
    call_claude_structured,
    load_chat_prompt,
)
from src.db.models.job import Job
from src.enums.claude_models import ClaudeModel
from src.enums.hard_filters import HARD_FILTER_WEIGHTS, HardFilterKey
from src.enums.prompts import HARD_FILTER_SYSTEM_PROMPT_BASE as SYSTEM_PROMPT_BASE
from src.enums.prompts import HARD_FILTER_SYSTEM_PROMPT_STRICT as SYSTEM_PROMPT_STRICT
from src.enums.score_types import ScoreModel
from src.schemas.hard_filter import (
    HardFilterClaudeResponse,
    HardFilterScoreResult,
    HardFilterSignalDetail,
)
from src.schemas.parsed_cv import ParsedCV

logger = logging.getLogger(__name__)

# --- Constants --------------------------------------------------------------

PROMPT_TEMPLATE: Final[str] = "hard_filter_score.j2"

# Distinct cache-key tags so the strict retry never serves the cached
# bad-output of the base prompt. Bump the ``v1`` segment when the system
# prompt's semantics change.
# v3 = GCC/bilingual + evidence-first system prompt; per-signal ``evidence``
# quote and optional ``gap`` added to the response schema.
# v4 = Regional Experience rubric tightened: assesses ONLY work history in the
# required region; explicitly forbids referencing current residence, home
# address, phone/country code, nationality, or visa in the score/evidence/gap.
PROMPT_VERSION_BASE: Final[str] = "hard_filter_score:base:v4"
PROMPT_VERSION_STRICT: Final[str] = "hard_filter_score:strict:v4"

# Hard cap on response length. Five score+reasoning pairs with generous
# CoT should never exceed this. Keeps cost predictable.
MAX_TOKENS: Final[int] = 4096

# Caps on the CV text and the JD text shoved into the prompt. Same
# limit on both — generous, but bounded.
MAX_CV_TEXT_CHARS: Final[int] = 20_000
MAX_JD_TEXT_CHARS: Final[int] = 20_000


# ``SYSTEM_PROMPT_BASE`` / ``SYSTEM_PROMPT_STRICT`` are imported from
# ``src.enums.prompts`` — data-driven from ``HardFilterKey`` /
# ``HARD_FILTER_RUBRICS`` so the enum and the prompt cannot drift apart.


# Built once: literal system turn + the Jinja human template. Base and strict
# share the human template but carry different system prompts, matching the two
# cache slots (PROMPT_VERSION_BASE / _STRICT).
_PROMPT_BASE: Final = load_chat_prompt(
    system=SYSTEM_PROMPT_BASE, human_template_file=PROMPT_TEMPLATE
)
_PROMPT_STRICT: Final = load_chat_prompt(
    system=SYSTEM_PROMPT_STRICT, human_template_file=PROMPT_TEMPLATE
)


class HardFilterScoringError(RuntimeError):
    """Raised when Claude fails both the base and strict-retry attempts.

    The worker catches this and writes ``pipeline_status['hard_filter'] =
    'failed'`` with the message as the error key, so HR can see the
    cause in the detail endpoint without crashing the task.
    """


# --- Helpers ----------------------------------------------------------------


def _truncate(text: str, limit: int) -> str:
    return text[:limit] if len(text) > limit else text


def _parsed_cv_summary_json(parsed_cv: ParsedCV) -> str:
    """JSON-serialise the parsed CV in a stable form for the prompt.

    Same shape as the authenticity judge so cache keys are independent
    of unstable dict ordering.
    """
    payload = parsed_cv.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2)


def _job_summary_for_prompt(job: Job) -> dict[str, Any]:
    """Pluck the JD fields the rubric needs onto a stable dict.

    Skipping ``jd_embedding`` and large free-text columns (handled
    separately) keeps the rendered prompt deterministic and the cache
    key stable across runs.
    """
    return {
        "title": job.title,
        "hiring_company": job.hiring_company,
        "country": job.country,
        "city": job.city,
        "employment_type": (job.employment_type.value if job.employment_type is not None else None),
        "work_mode": job.work_mode.value if job.work_mode is not None else None,
        "min_experience_years": job.min_experience_years,
        "required_skills": list(job.required_skills or []),
        "preferred_skills": list(job.preferred_skills or []),
        "visa_requirement": (
            job.visa_requirement.value if job.visa_requirement is not None else None
        ),
        "nationality_preference": list(job.nationality_preference or []),
        "languages_required": list(job.languages_required or []),
        "notice_period": job.notice_period.value if job.notice_period is not None else None,
    }


async def _call_scorer(
    *,
    variables: dict[str, Any],
    prompt: Any,
    prompt_version: str,
    use_cache: bool,
) -> HardFilterClaudeResponse:
    """Call Claude Opus for a schema-bound :class:`HardFilterClaudeResponse`.

    OPUS per ClaudeModel guidance: hard-filter / CV scoring is heavyweight
    multi-criteria rubric evaluation over long CV context. Raises
    :class:`ClaudeStructuredOutputError` if the response can't be bound.
    """
    return await call_claude_structured(
        model=ClaudeModel.OPUS,
        prompt=prompt,
        variables=variables,
        schema=HardFilterClaudeResponse,
        max_tokens=MAX_TOKENS,
        prompt_version=prompt_version,
        use_cache=use_cache,
    )


def _aggregate(
    validated: HardFilterClaudeResponse,
    *,
    prompt_version: str,
) -> HardFilterScoreResult:
    """Weighted-sum the per-signal scores into the final result.

    Weights are percentages (they sum to 100), so we divide the weighted
    sum by the total weight; with each score in 0-100 the total stays in
    the 0-100 range.
    """
    by_key = validated.as_dict()
    signals: dict[HardFilterKey, HardFilterSignalDetail] = {}
    weighted_sum = 0.0
    weight_total = 0.0
    for key, report in by_key.items():
        weight = HARD_FILTER_WEIGHTS[key]
        signals[key] = HardFilterSignalDetail(
            score=float(report.score),
            reasoning=report.reasoning,
            evidence=report.evidence,
            gap=report.gap,
            weight=weight,
        )
        weighted_sum += float(report.score) * weight
        weight_total += weight
    total = weighted_sum / weight_total if weight_total else 0.0
    return HardFilterScoreResult(
        total=total,
        signals=signals,
        prompt_version=prompt_version,
        model_used=ScoreModel.CLAUDE_OPUS.value,
    )


def _aggregate_and_log(
    validated: HardFilterClaudeResponse, *, prompt_version: str, retried: bool
) -> HardFilterScoreResult:
    """Aggregate the per-signal scores and emit the PII-safe success log line."""
    result = _aggregate(validated, prompt_version=prompt_version)
    logger.info(
        "hard_filter.scored",
        extra={
            "model": ClaudeModel.OPUS.value,
            "retried": retried,
            "total": round(result.total, 2),
        },
    )
    return result


# --- Public API -------------------------------------------------------------


async def score_hard_filters(
    *,
    job: Job,
    parsed_cv: ParsedCV,
    cv_text: str,
    use_cache: bool = True,
) -> HardFilterScoreResult:
    """Score one (job, CV) pair against the 5-signal hard-filter rubric.

    Returns a :class:`HardFilterScoreResult` ready to persist to
    ``applications.hard_filter_score`` (total),
    ``applications.hard_filter_breakdown`` (signals), and
    ``application_scores`` (audit row).

    Args:
        job: The job spec the candidate is being scored against.
        parsed_cv: The candidate's structured CV (Step 3.4 output).
        cv_text: The candidate's raw CV text (Step 3.3 output).
        use_cache: Forwarded to :func:`call_claude_structured`. ``False``
            bypasses the Claude Redis cache so a Step 4.4 rescore can force
            a fresh response. Defaults to ``True``.

    Raises:
        HardFilterScoringError: when both the base attempt and the
            strict-retry attempt fail to bind to the schema. The worker
            maps this to a ``pipeline_status['hard_filter'] = 'failed'`` row.
        anthropic.* (transient): rate-limit, overloaded, connection
            errors are NOT caught here; the Celery worker's
            ``autoretry_for`` handles them.
    """
    job_summary = _job_summary_for_prompt(job)
    job_description = _truncate(job.job_description, MAX_JD_TEXT_CHARS)
    parsed_cv_json = _parsed_cv_summary_json(parsed_cv)
    truncated_cv_text = _truncate(cv_text, MAX_CV_TEXT_CHARS)

    # The JD fields are flattened to top-level template variables (LangChain's
    # jinja2 sandbox forbids attribute access like ``job.title``).
    variables: dict[str, Any] = {
        **job_summary,
        "job_description": job_description,
        "parsed_cv_json": parsed_cv_json,
        "cv_text": truncated_cv_text,
    }

    # --- First attempt: base prompt -----------------------------------------
    first_failure_reason: str | None = None
    try:
        validated = await _call_scorer(
            variables=variables,
            prompt=_PROMPT_BASE,
            prompt_version=PROMPT_VERSION_BASE,
            use_cache=use_cache,
        )
    except ClaudeStructuredOutputError:
        first_failure_reason = "structured_bind_failed"
    else:
        return _aggregate_and_log(validated, prompt_version=PROMPT_VERSION_BASE, retried=False)

    logger.warning(
        "hard_filter.validation_failed_retrying",
        extra={
            "model": ClaudeModel.OPUS.value,
            "reason": first_failure_reason,
        },
    )

    # --- Second attempt: strict prompt --------------------------------------
    second_failure_reason: str | None = None
    try:
        validated = await _call_scorer(
            variables=variables,
            prompt=_PROMPT_STRICT,
            prompt_version=PROMPT_VERSION_STRICT,
            use_cache=use_cache,
        )
    except ClaudeStructuredOutputError:
        second_failure_reason = "structured_bind_failed"
    else:
        return _aggregate_and_log(validated, prompt_version=PROMPT_VERSION_STRICT, retried=True)

    # Both attempts failed permanently. Raise so the worker can record
    # the failure on pipeline_status.
    reason = second_failure_reason or first_failure_reason or "unknown"
    logger.warning(
        "hard_filter.permanent_failure",
        extra={
            "model": ClaudeModel.OPUS.value,
            "first_reason": first_failure_reason,
            "second_reason": second_failure_reason,
        },
    )
    raise HardFilterScoringError(f"claude_failed_twice: {reason}")
