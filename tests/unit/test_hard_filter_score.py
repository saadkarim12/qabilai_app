"""Unit tests for Step 4.3's hard-filter scoring service.

The structured Claude call is stubbed by default so the tests are fast,
deterministic, and don't touch Anthropic / Redis. A success outcome hands back
a validated ``HardFilterClaudeResponse``; a failure outcome raises
``ClaudeStructuredOutputError`` (what now drives the retry / permanent-failure
path). Per-signal schema rules are validated directly against the schema. One
test (``test_real_template_renders``) renders the real ``hard_filter_score.j2``.

Doc acceptance criteria covered (KABIL_AI_ARCHITECTURE.md §4.3):

* Sample CV vs JD produces 5 signal scores
* Weighted total saved
* Breakdown JSON includes reasoning per signal
* Failed Claude output triggers retry, then surfaces error via app status
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from src.ai.claude import ClaudeStructuredOutputError
from src.db.models.job import Job
from src.enums.claude_models import ClaudeModel
from src.enums.hard_filters import HARD_FILTER_WEIGHTS, HardFilterKey
from src.enums.jobs import EmploymentType, JobStatus, WorkMode
from src.enums.prompts import HARD_FILTER_SYSTEM_PROMPT_BASE
from src.enums.score_types import ScoreModel
from src.schemas.hard_filter import (
    HardFilterClaudeResponse,
    HardFilterScoreResult,
    HardFilterSignalReport,
)
from src.schemas.parsed_cv import ParsedCV, WorkEntry
from src.services.scoring import hard_filter_service
from src.services.scoring.hard_filter_service import (
    MAX_CV_TEXT_CHARS,
    MAX_TOKENS,
    PROMPT_VERSION_BASE,
    PROMPT_VERSION_STRICT,
    HardFilterScoringError,
    score_hard_filters,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _good_response(
    *,
    skills: int = 88,
    experience: int = 82,
    industry: int = 70,
    regional: int = 60,
    role_seniority: int = 78,
) -> dict[str, Any]:
    return {
        "skills": {
            "score": skills,
            "evidence": "'Built FastAPI services on PostgreSQL' — Acme Corp.",
            "reasoning": "Strong match on Python and Postgres.",
            "gap": "No Kubernetes evidence.",
        },
        "experience": {
            "score": experience,
            "evidence": "6.5 years across two senior backend roles.",
            "reasoning": "6 years vs 5 required; senior trajectory.",
            "gap": None,
        },
        "industry": {
            "score": industry,
            "evidence": "SaaS product roles listed throughout.",
            "reasoning": "Adjacent SaaS background.",
        },
        "regional": {
            "score": regional,
            "evidence": "No GCC-based employer appears in the work history.",
            "reasoning": "Same time zone; visa sponsorship needed.",
            "gap": "No UAE/KSA on-the-ground experience.",
        },
        "role_seniority": {
            "score": role_seniority,
            "evidence": "Most recent title: Senior Backend Engineer.",
            "reasoning": "Senior IC track aligned with the role.",
        },
    }


def _resp(**kwargs: Any) -> HardFilterClaudeResponse:
    """A validated scorer response — what a successful structured call returns."""
    return HardFilterClaudeResponse.model_validate(_good_response(**kwargs))


def _fail() -> ClaudeStructuredOutputError:
    """A bind-failure outcome (drives retry / permanent failure)."""
    return ClaudeStructuredOutputError("bind failed")


def _sample_job() -> Job:
    return Job(
        title="Senior Backend Engineer",
        hiring_company="Kabil Test Co",
        country="AE",
        city="Dubai",
        employment_type=EmploymentType.PERMANENT,
        work_mode=WorkMode.HYBRID,
        currency="AED",
        min_salary=20_000,
        max_salary=30_000,
        notice_period=None,
        min_experience_years=5,
        required_skills=["Python", "PostgreSQL"],
        preferred_skills=["AWS"],
        visa_requirement=None,
        nationality_preference=[],
        languages_required=["english"],
        job_description="Build the Kabil hiring backend.",
        status=JobStatus.OPEN,
        public_slug="abcdef0123456789",
    )


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
    monkeypatch: pytest.MonkeyPatch, *, outcomes: list[Any]
) -> list[dict[str, Any]]:
    """Replace ``call_claude_structured`` with successive ``outcomes``.

    Each entry is a ``HardFilterClaudeResponse`` (returned) or a
    ``ClaudeStructuredOutputError`` (raised). Captures kwargs for wiring
    assertions.
    """
    captured: list[dict[str, Any]] = []
    pending = list(outcomes)

    async def _fake(**kwargs: Any) -> HardFilterClaudeResponse:
        captured.append(kwargs)
        if not pending:
            raise AssertionError("call_claude_structured called more times than outcomes provided")
        item = pending.pop(0)
        if isinstance(item, ClaudeStructuredOutputError):
            raise item
        return item

    monkeypatch.setattr(hard_filter_service, "call_claude_structured", _fake)
    return captured


# ---------------------------------------------------------------------------
# Schema-level tests
# ---------------------------------------------------------------------------


def test_signal_report_rejects_score_above_100() -> None:
    with pytest.raises(ValidationError):
        HardFilterSignalReport(score=101, reasoning="too high", evidence="quote")


def test_signal_report_rejects_empty_reasoning() -> None:
    with pytest.raises(ValidationError):
        HardFilterSignalReport(score=80, reasoning="", evidence="quote")


def test_signal_report_requires_evidence() -> None:
    """Evidence-first rule: a signal with no evidence quote fails the schema
    (which triggers the strict retry through the structured layer)."""
    with pytest.raises(ValidationError):
        HardFilterSignalReport.model_validate({"score": 80, "reasoning": "ok"})
    with pytest.raises(ValidationError):
        HardFilterSignalReport(score=80, reasoning="ok", evidence="")


def test_signal_report_gap_is_optional() -> None:
    report = HardFilterSignalReport(score=80, reasoning="ok", evidence="quote")
    assert report.gap is None


def test_claude_response_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        HardFilterClaudeResponse.model_validate({**_good_response(), "extra": "nope"})


def test_claude_response_rejects_missing_signal() -> None:
    partial = _good_response()
    del partial["industry"]
    with pytest.raises(ValidationError):
        HardFilterClaudeResponse.model_validate(partial)


def test_claude_response_as_dict_is_keyed_by_signal_enum() -> None:
    resp = HardFilterClaudeResponse.model_validate(_good_response())
    by_key = resp.as_dict()
    assert set(by_key.keys()) == set(HardFilterKey)


# ---------------------------------------------------------------------------
# Aggregation math
# ---------------------------------------------------------------------------


async def test_weighted_total_matches_hand_calculation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Total must equal sum(score_i * weight_i) / sum(weights). Doc weights
    (percentages out of 100): 35/35/10/10/10."""
    _patch_structured(
        monkeypatch,
        outcomes=[
            _resp(
                skills=80,
                experience=70,
                industry=60,
                regional=50,
                role_seniority=40,
            )
        ],
    )
    result = await score_hard_filters(
        job=_sample_job(),
        parsed_cv=_sample_cv(),
        cv_text="Built backend systems.",
    )
    # (80*35 + 70*35 + 60*10 + 50*10 + 40*10) / 100
    #   = (2800 + 2450 + 600 + 500 + 400) / 100 = 6750 / 100 = 67.5
    assert result.total == pytest.approx(67.5, rel=1e-9)
    assert isinstance(result, HardFilterScoreResult)


async def test_breakdown_has_one_entry_per_signal_with_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc acceptance: 'Breakdown JSON includes reasoning per signal'."""
    _patch_structured(monkeypatch, outcomes=[_resp()])
    result = await score_hard_filters(
        job=_sample_job(),
        parsed_cv=_sample_cv(),
        cv_text="Built backend systems.",
    )
    assert set(result.signals.keys()) == set(HardFilterKey)
    for key, detail in result.signals.items():
        assert detail.reasoning  # non-empty string
        assert detail.weight == HARD_FILTER_WEIGHTS[key]


async def test_breakdown_carries_evidence_and_gap_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The grounding evidence quote and optional gap survive aggregation into
    the persisted breakdown (both are serialised onto the JSONB columns)."""
    _patch_structured(monkeypatch, outcomes=[_resp()])
    result = await score_hard_filters(
        job=_sample_job(),
        parsed_cv=_sample_cv(),
        cv_text="Built backend systems.",
    )
    skills = result.signals[HardFilterKey.SKILLS]
    assert skills.evidence  # the CV quote grounding the score
    assert skills.gap == "No Kubernetes evidence."
    # A signal whose report carried no gap comes through as None.
    assert result.signals[HardFilterKey.INDUSTRY].gap is None
    # model_dump (what the worker persists) includes the new keys.
    dumped = skills.model_dump(mode="json")
    assert "evidence" in dumped and "gap" in dumped


# ---------------------------------------------------------------------------
# Happy path: single base-prompt call
# ---------------------------------------------------------------------------


async def test_first_attempt_success_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_structured(monkeypatch, outcomes=[_resp()])

    result = await score_hard_filters(
        job=_sample_job(),
        parsed_cv=_sample_cv(),
        cv_text="Built backend systems.",
    )

    assert result.model_used == ScoreModel.CLAUDE_OPUS.value
    assert result.prompt_version == PROMPT_VERSION_BASE
    assert len(captured) == 1
    assert captured[0]["model"] is ClaudeModel.OPUS
    assert captured[0]["prompt_version"] == PROMPT_VERSION_BASE
    assert captured[0]["schema"] is HardFilterClaudeResponse
    assert captured[0]["max_tokens"] == MAX_TOKENS
    assert captured[0]["prompt"] is hard_filter_service._PROMPT_BASE
    # Cache is on by default (rescore passes use_cache=False to bypass it).
    assert captured[0]["use_cache"] is True


async def test_use_cache_false_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Step 4.4 rescore bypass must reach the Claude wrapper."""
    captured = _patch_structured(monkeypatch, outcomes=[_resp()])
    await score_hard_filters(
        job=_sample_job(),
        parsed_cv=_sample_cv(),
        cv_text="Built backend systems.",
        use_cache=False,
    )
    assert captured[0]["use_cache"] is False


# ---------------------------------------------------------------------------
# Retry path
# ---------------------------------------------------------------------------


async def test_bind_failure_triggers_strict_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First reply can't bind (missing signal / extra key / bad score) → strict
    prompt is re-issued and succeeds. Schema-cause coverage lives in the schema
    tests above; the structured layer surfaces all of them as one bind failure."""
    captured = _patch_structured(monkeypatch, outcomes=[_fail(), _resp()])

    result = await score_hard_filters(
        job=_sample_job(),
        parsed_cv=_sample_cv(),
        cv_text="Built backend systems.",
    )
    assert result.prompt_version == PROMPT_VERSION_STRICT
    assert len(captured) == 2
    assert captured[0]["prompt_version"] == PROMPT_VERSION_BASE
    assert captured[0]["prompt"] is hard_filter_service._PROMPT_BASE
    assert captured[1]["prompt_version"] == PROMPT_VERSION_STRICT
    assert captured[1]["prompt"] is hard_filter_service._PROMPT_STRICT


# ---------------------------------------------------------------------------
# Permanent failure
# ---------------------------------------------------------------------------


async def test_double_failure_raises_hard_filter_scoring_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc acceptance: 'Failed Claude output triggers retry, then surfaces error'."""
    _patch_structured(monkeypatch, outcomes=[_fail(), _fail()])

    with pytest.raises(HardFilterScoringError):
        await score_hard_filters(
            job=_sample_job(),
            parsed_cv=_sample_cv(),
            cv_text="Built backend systems.",
        )


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


async def test_oversized_cv_text_truncated_before_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc-stable behaviour: CV text > MAX_CV_TEXT_CHARS is hard-clipped."""
    captured = _patch_structured(monkeypatch, outcomes=[_resp()])

    long_cv = "a" * (MAX_CV_TEXT_CHARS + 5_000)
    await score_hard_filters(
        job=_sample_job(),
        parsed_cv=_sample_cv(),
        cv_text=long_cv,
    )
    assert len(captured) == 1
    assert len(captured[0]["variables"]["cv_text"]) == MAX_CV_TEXT_CHARS


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------


async def test_real_template_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render the captured prompt — surfaces Jinja errors in hard_filter_score.j2."""
    captured = _patch_structured(monkeypatch, outcomes=[_resp()])
    await score_hard_filters(
        job=_sample_job(),
        parsed_cv=_sample_cv(),
        cv_text="Cut p95 latency 800ms → 200ms across 4 services.",
    )

    call = captured[0]
    rendered = call["prompt"].invoke(call["variables"]).to_messages()[1].content
    assert "Senior Backend Engineer" in rendered
    assert "Cut p95 latency 800ms" in rendered
    assert "Build the Kabil hiring backend." in rendered


def test_system_prompt_has_gcc_bilingual_and_evidence_framing() -> None:
    """The base system prompt carries the GCC/bilingual/evidence-first framing
    and the response schema advertises the new evidence + gap fields."""
    prompt = HARD_FILTER_SYSTEM_PROMPT_BASE
    assert "UAE" in prompt and "KSA" in prompt
    assert "EVIDENCE-FIRST RULE" in prompt
    assert "BILINGUAL RULE" in prompt
    # The generated OUTPUT FORMAT schema block advertises the new fields.
    assert '"evidence"' in prompt
    assert '"gap"' in prompt
