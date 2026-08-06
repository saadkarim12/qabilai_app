"""Unit tests for the authenticity LLM judge.

The structured Claude call is stubbed by default so the tests are fast,
deterministic, and don't touch Anthropic / Redis. A success outcome hands back
a validated response schema; a failure outcome raises
``ClaudeStructuredOutputError`` (what drives the retry / fallback path).

Two calibrations: baseline (five job-agnostic signals, no job) and job-aware
(six signals, with job context). One test renders the real
``authenticity_judge.j2``.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.ai.claude import ClaudeStructuredOutputError
from src.enums.authenticity_signals import BASELINE_SIGNAL_KEYS, SignalComputedBy, SignalKey
from src.enums.claude_models import ClaudeModel
from src.schemas.authenticity_judge import (
    JUDGE_FINDING_MAX_LENGTH,
    AuthenticityBaselineResponse,
    AuthenticityJobAwareResponse,
    JudgeSignalReport,
)
from src.schemas.parsed_cv import ParsedCV, WorkEntry
from src.services.authenticity import llm_judge
from src.services.authenticity.llm_judge import (
    JUDGE_FAILURE_SCORE,
    MAX_CV_TEXT_CHARS,
    MAX_TOKENS,
    PROMPT_TEMPLATE,
    PROMPT_VERSION_BASELINE_BASE,
    PROMPT_VERSION_BASELINE_STRICT,
    PROMPT_VERSION_JOBAWARE_BASE,
    PROMPT_VERSION_JOBAWARE_STRICT,
    judge_authenticity,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _report(score: int, finding: str) -> dict[str, Any]:
    return {"score": score, "finding": finding}


def _good_response(*, job_aware: bool, **overrides: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "specificity": _report(88, "Concrete numbers and named systems throughout."),
        "timeline_coherence": _report(85, "All skills appear in dated roles."),
        "consistency": _report(90, "Title, skills, and years line up cleanly."),
        "linguistic_genericity": _report(80, "Natural, concrete language."),
        "structural_templating": _report(78, "Varied bullet lengths, no template tells."),
        "rationale": "Reads authentic overall; strongest signal is specificity.",
    }
    if job_aware:
        payload["jd_keyword_mirroring"] = _report(82, "Idiosyncratic, role-specific phrasing.")
    payload.update(overrides)
    return payload


def _resp(*, job_aware: bool = False, **overrides: dict[str, Any]) -> AuthenticityBaselineResponse:
    schema = AuthenticityJobAwareResponse if job_aware else AuthenticityBaselineResponse
    return schema.model_validate(_good_response(job_aware=job_aware, **overrides))


def _fail() -> ClaudeStructuredOutputError:
    return ClaudeStructuredOutputError("bind failed")


def _fail_with_args(payload: dict[str, Any]) -> ClaudeStructuredOutputError:
    """A bind failure that still carries the model's tool args (salvageable)."""
    return ClaudeStructuredOutputError("bind failed", raw_args=payload)


def _sample_cv() -> ParsedCV:
    return ParsedCV(
        full_name="Layla Hassan",
        email="layla.hassan@example.com",
        skills=["Python", "PostgreSQL", "FastAPI"],
        work_history=[
            WorkEntry(
                company="Acme Corp",
                title="Senior Backend Engineer",
                start="Jan 2020",
                end=None,
                description="Cut p95 latency from 800ms to 200ms across 4 services.",
            )
        ],
        total_experience_years=6.5,
        languages=["English", "Arabic"],
    )


def _patch_structured(
    monkeypatch: pytest.MonkeyPatch,
    *,
    outcomes: list[Any],
) -> list[dict[str, Any]]:
    """Replace ``call_claude_structured`` with successive ``outcomes``."""
    captured: list[dict[str, Any]] = []
    pending = list(outcomes)

    async def _fake(**kwargs: Any) -> Any:
        captured.append(kwargs)
        if not pending:
            raise AssertionError("call_claude_structured called more times than outcomes provided")
        item = pending.pop(0)
        if isinstance(item, ClaudeStructuredOutputError):
            raise item
        return item

    monkeypatch.setattr(llm_judge, "call_claude_structured", _fake)
    return captured


# ---------------------------------------------------------------------------
# Schema-level tests
# ---------------------------------------------------------------------------


def test_judge_signal_report_rejects_score_above_100() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JudgeSignalReport(score=101, finding="too high")


def test_judge_signal_report_rejects_empty_finding() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JudgeSignalReport(score=80, finding="")


def test_judge_signal_report_rejects_overlong_finding() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JudgeSignalReport(score=80, finding="x" * (JUDGE_FINDING_MAX_LENGTH + 1))


def test_baseline_response_rejects_jd_mirroring_key() -> None:
    """The five-signal schema forbids the sixth signal (extra='forbid')."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AuthenticityBaselineResponse.model_validate(_good_response(job_aware=True))


def test_jobaware_response_requires_jd_mirroring_key() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AuthenticityJobAwareResponse.model_validate(_good_response(job_aware=False))


# ---------------------------------------------------------------------------
# Baseline calibration (no job)
# ---------------------------------------------------------------------------


async def test_baseline_scores_five_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_structured(monkeypatch, outcomes=[_resp()])
    result = await judge_authenticity(cv_text="A CV.", parsed_cv=_sample_cv())

    assert set(result.signals.keys()) == set(BASELINE_SIGNAL_KEYS)
    assert SignalKey.JD_KEYWORD_MIRRORING not in result.signals
    assert result.signals[SignalKey.SPECIFICITY].score == 88.0
    assert result.signals[SignalKey.SPECIFICITY].finding is not None
    assert result.signals[SignalKey.SPECIFICITY].computed_by is SignalComputedBy.CLAUDE
    assert result.rationale is not None

    assert len(captured) == 1
    assert captured[0]["model"] is ClaudeModel.SONNET
    assert captured[0]["prompt_version"] == PROMPT_VERSION_BASELINE_BASE
    assert captured[0]["schema"] is AuthenticityBaselineResponse
    assert captured[0]["max_tokens"] == MAX_TOKENS
    assert captured[0]["prompt"] is llm_judge._PROMPT_BASELINE_BASE


async def test_concern_flag_derived_from_score(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_structured(
        monkeypatch,
        outcomes=[_resp(specificity=_report(30, "No numbers, generic verbs throughout."))],
    )
    result = await judge_authenticity(cv_text="Vague CV.", parsed_cv=_sample_cv())
    assert result.signals[SignalKey.SPECIFICITY].score == 30.0
    assert result.signals[SignalKey.SPECIFICITY].concern is True
    # A high signal is not a concern.
    assert result.signals[SignalKey.CONSISTENCY].concern is False


# ---------------------------------------------------------------------------
# Job-aware calibration (with job)
# ---------------------------------------------------------------------------


async def test_jobaware_scores_six_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_structured(monkeypatch, outcomes=[_resp(job_aware=True)])
    result = await judge_authenticity(
        cv_text="A CV.",
        parsed_cv=_sample_cv(),
        job_title="Senior Backend Engineer",
        required_skills=["Python", "AWS"],
    )
    assert set(result.signals.keys()) == set(SignalKey)
    assert result.signals[SignalKey.JD_KEYWORD_MIRRORING].score == 82.0

    assert captured[0]["prompt_version"] == PROMPT_VERSION_JOBAWARE_BASE
    assert captured[0]["schema"] is AuthenticityJobAwareResponse
    assert captured[0]["prompt"] is llm_judge._PROMPT_JOBAWARE_BASE
    # Job context flows into the prompt variables.
    variables = captured[0]["variables"]
    assert variables["job_title"] == "Senior Backend Engineer"
    assert variables["required_skills"] == "Python, AWS"


# ---------------------------------------------------------------------------
# Retry + fallback
# ---------------------------------------------------------------------------


async def test_bind_failure_triggers_strict_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_structured(monkeypatch, outcomes=[_fail(), _resp()])
    result = await judge_authenticity(cv_text="CV", parsed_cv=_sample_cv())
    assert len(captured) == 2
    assert captured[0]["prompt_version"] == PROMPT_VERSION_BASELINE_BASE
    assert captured[1]["prompt_version"] == PROMPT_VERSION_BASELINE_STRICT
    assert captured[1]["prompt"] is llm_judge._PROMPT_BASELINE_STRICT
    assert result.signals[SignalKey.SPECIFICITY].computed_by is SignalComputedBy.CLAUDE


async def test_jobaware_retry_uses_jobaware_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_structured(monkeypatch, outcomes=[_fail(), _resp(job_aware=True)])
    await judge_authenticity(
        cv_text="CV", parsed_cv=_sample_cv(), job_title="Eng", required_skills=["Python"]
    )
    assert captured[1]["prompt_version"] == PROMPT_VERSION_JOBAWARE_STRICT
    assert captured[1]["prompt"] is llm_judge._PROMPT_JOBAWARE_STRICT


async def test_two_failures_fall_back_to_judge_failure_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_structured(monkeypatch, outcomes=[_fail(), _fail()])
    result = await judge_authenticity(cv_text="CV", parsed_cv=_sample_cv())
    assert len(captured) == 2
    # Baseline calibration → five signals all at the failure score.
    assert set(result.signals.keys()) == set(BASELINE_SIGNAL_KEYS)
    for signal in result.signals.values():
        assert signal.score == JUDGE_FAILURE_SCORE
        assert signal.details["judge_failed"] is True
        assert signal.computed_by is SignalComputedBy.CLAUDE
    assert result.rationale is None


async def test_jobaware_fallback_covers_six_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_structured(monkeypatch, outcomes=[_fail(), _fail()])
    result = await judge_authenticity(
        cv_text="CV", parsed_cv=_sample_cv(), job_title="Eng", required_skills=["Python"]
    )
    assert set(result.signals.keys()) == set(SignalKey)


# ---------------------------------------------------------------------------
# Salvage: repair an over-long finding instead of retrying / falling back
# ---------------------------------------------------------------------------


async def test_overlong_finding_is_salvaged_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The real-world failure: a valid reply whose only fault is one finding
    # past the cap (skill-dense CVs overflow ``timeline_coherence``).
    overlong = "e" * (JUDGE_FINDING_MAX_LENGTH + 250)
    payload = _good_response(job_aware=False, timeline_coherence=_report(72, overlong))
    captured = _patch_structured(monkeypatch, outcomes=[_fail_with_args(payload)])
    result = await judge_authenticity(cv_text="CV", parsed_cv=_sample_cv())

    # Only ONE Claude call — the ~40s strict retry never fired.
    assert len(captured) == 1
    # The offending finding was truncated to the cap; the real score survived.
    salvaged = result.signals[SignalKey.TIMELINE_COHERENCE]
    assert salvaged.score == 72.0
    assert salvaged.finding is not None
    assert len(salvaged.finding) == JUDGE_FINDING_MAX_LENGTH
    # Not the neutral all-50 fallback.
    assert result.rationale is not None
    assert salvaged.details.get("judge_failed") is not True


async def test_overlong_finding_salvaged_on_strict_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Base attempt carries no args (unsalvageable) → strict retry, whose args
    # are salvageable → still no neutral fallback.
    overlong = "e" * (JUDGE_FINDING_MAX_LENGTH + 100)
    payload = _good_response(job_aware=False, consistency=_report(65, overlong))
    captured = _patch_structured(monkeypatch, outcomes=[_fail(), _fail_with_args(payload)])
    result = await judge_authenticity(cv_text="CV", parsed_cv=_sample_cv())

    assert len(captured) == 2
    assert result.signals[SignalKey.CONSISTENCY].score == 65.0
    assert result.rationale is not None


async def test_malformed_args_are_not_salvaged(monkeypatch: pytest.MonkeyPatch) -> None:
    # A payload missing a required signal can't be trusted → no salvage, so the
    # base failure escalates to the strict retry as before.
    payload = _good_response(job_aware=False)
    del payload["consistency"]
    captured = _patch_structured(monkeypatch, outcomes=[_fail_with_args(payload), _resp()])
    result = await judge_authenticity(cv_text="CV", parsed_cv=_sample_cv())

    assert len(captured) == 2  # fell through to the strict retry
    assert result.signals[SignalKey.CONSISTENCY].score == 90.0


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------


async def test_cv_text_truncated_at_max_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_structured(monkeypatch, outcomes=[_resp()])
    long_cv = "x" * (MAX_CV_TEXT_CHARS + 1000)
    await judge_authenticity(cv_text=long_cv, parsed_cv=_sample_cv())
    assert len(captured[0]["variables"]["cv_text"]) == MAX_CV_TEXT_CHARS


async def test_parsed_cv_serialized_into_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_structured(monkeypatch, outcomes=[_resp()])
    await judge_authenticity(cv_text="anything", parsed_cv=_sample_cv())
    parsed_cv_json = captured[0]["variables"]["parsed_cv_json"]
    assert "Acme Corp" in parsed_cv_json
    assert "Senior Backend Engineer" in parsed_cv_json


async def test_baseline_variables_have_empty_job_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_structured(monkeypatch, outcomes=[_resp()])
    await judge_authenticity(cv_text="CV", parsed_cv=_sample_cv())
    assert captured[0]["variables"]["job_title"] == ""
    assert captured[0]["variables"]["required_skills"] == ""


# ---------------------------------------------------------------------------
# Real template
# ---------------------------------------------------------------------------


async def test_real_template_renders_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_structured(monkeypatch, outcomes=[_resp()])
    await judge_authenticity(cv_text="A real CV text body.", parsed_cv=_sample_cv())
    call = captured[0]
    rendered = call["prompt"].invoke(call["variables"]).to_messages()[1].content
    assert "A real CV text body." in rendered
    assert "Acme Corp" in rendered
    # No job block in the baseline calibration.
    assert "JOB CONTEXT" not in rendered


async def test_real_template_renders_job_block(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_structured(monkeypatch, outcomes=[_resp(job_aware=True)])
    await judge_authenticity(
        cv_text="CV body.",
        parsed_cv=_sample_cv(),
        job_title="Cloud Architect",
        required_skills=["AWS"],
    )
    call = captured[0]
    rendered = call["prompt"].invoke(call["variables"]).to_messages()[1].content
    assert "JOB CONTEXT" in rendered
    assert "Cloud Architect" in rendered


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_constants_pinned() -> None:
    assert PROMPT_TEMPLATE == "authenticity_judge.j2"
    assert PROMPT_VERSION_BASELINE_BASE == "authenticity_judge:baseline:base:v4"
    assert PROMPT_VERSION_JOBAWARE_BASE == "authenticity_judge:jobaware:base:v4"
    assert MAX_TOKENS == 4096
    assert MAX_CV_TEXT_CHARS == 20_000
    assert JUDGE_FAILURE_SCORE == 50.0


def test_sonnet_model_id_pinned() -> None:
    assert ClaudeModel.SONNET.value == "claude-sonnet-4-6"
