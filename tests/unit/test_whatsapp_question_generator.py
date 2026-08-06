"""Unit tests for the WhatsApp question generator.

The structured Claude call is stubbed by default — fast, deterministic, and
independent of Anthropic / Redis / filesystem. Per-item schema rules (category
enum, non-empty bilingual text, no stray keys) are validated directly against
``WhatsAppQuestionDraftList``. One test (``test_real_template_renders_with_job``)
renders the real ``generate_whatsapp_questions.j2`` so it catches Jinja
regressions.

The generator composes the final list from two sources: the deterministic
fixed question set (identical for every job, ``ai_verifies_response=False``)
followed by up to ``MAX_AI_QUESTIONS`` Claude-authored ``background_validation``
questions (``ai_verifies_response=True`` — the only AI-scored ones).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from src.ai import whatsapp_question_generator as gen
from src.ai.claude import ClaudeStructuredOutputError
from src.db.models.job import Job
from src.enums.claude_models import ClaudeModel
from src.enums.fixed_screening_questions import FIXED_QUESTION_KEYS
from src.enums.jobs import (
    EmploymentType,
    JobStatus,
    NoticePeriod,
    VisaRequirement,
    WorkMode,
)
from src.enums.whatsapp_questions import QuestionCategory
from src.schemas.whatsapp_question import (
    WhatsAppQuestion,
    WhatsAppQuestionDraft,
    WhatsAppQuestionDraftList,
)

# Number of fixed questions emitted for every job.
N_FIXED = len(FIXED_QUESTION_KEYS)

# --- Helpers ----------------------------------------------------------------


def _build_job(**overrides: Any) -> Job:
    """Build an in-memory Job ORM object — no DB required."""
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "created_by": uuid.uuid4(),
        "title": "Senior Backend Engineer",
        "hiring_company": "Kabil Test Corp",
        "country": "AE",
        "city": "Dubai",
        "employment_type": EmploymentType.PERMANENT,
        "work_mode": WorkMode.HYBRID,
        "currency": "AED",
        "min_salary": 20000,
        "max_salary": 30000,
        "notice_period": NoticePeriod.DAYS_30,
        "min_experience_years": 5,
        "required_skills": ["Python", "PostgreSQL"],
        "preferred_skills": ["AWS"],
        "visa_requirement": VisaRequirement.SPONSORSHIP_OFFERED,
        "nationality_preference": [],
        "languages_required": ["english", "arabic"],
        "ai_verify_flags": {},
        "job_description": "Build the Kabil hiring backend. Strong async Python.",
        "status": JobStatus.DRAFT,
        "public_slug": "abc123def456ghij",
    }
    base.update(overrides)
    return Job(**base)


def _sample_draft(**overrides: Any) -> dict[str, Any]:
    """A background_validation AI draft (the only kind Claude should return)."""
    base: dict[str, Any] = {
        "category": QuestionCategory.BACKGROUND_VALIDATION.value,
        "subcategory": "Current role",
        "question_en": "Can you confirm your current employer and role?",
        "question_ar": "هل يمكنك تأكيد صاحب العمل الحالي ودورك؟",
        "reasoning": "Confirms the most recent role claimed on the CV.",
    }
    base.update(overrides)
    return base


def _wrap(*draft_dicts: dict[str, Any]) -> WhatsAppQuestionDraftList:
    """Build the validated wrapper that the structured call would return."""
    return WhatsAppQuestionDraftList(questions=[WhatsAppQuestionDraft(**d) for d in draft_dicts])


def _patch_structured(
    monkeypatch: pytest.MonkeyPatch,
    *,
    results: list[WhatsAppQuestionDraftList],
) -> list[dict[str, Any]]:
    """Replace ``call_claude_structured``; return successive ``results``.

    Call N returns ``results[N]`` (the last entry repeats for extra calls),
    capturing each call's kwargs for wiring assertions.
    """
    captured: list[dict[str, Any]] = []

    async def _fake(**kwargs: Any) -> WhatsAppQuestionDraftList:
        captured.append(kwargs)
        return results[min(len(captured) - 1, len(results) - 1)]

    monkeypatch.setattr(gen, "call_claude_structured", _fake)
    return captured


@pytest.fixture
def deterministic_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mint IDs as q_id000001, q_id000002, … so assertions are stable."""
    counter = {"n": 0}

    def _fake() -> str:
        counter["n"] += 1
        return f"q_id{counter['n']:06d}"  # q_ + 8 chars

    monkeypatch.setattr(gen, "_mint_question_id", _fake)


# --- Composition: fixed questions then AI background ------------------------


async def test_composes_fixed_questions_then_ai_background(
    monkeypatch: pytest.MonkeyPatch, deterministic_ids: None
) -> None:
    _patch_structured(
        monkeypatch,
        results=[
            _wrap(
                _sample_draft(subcategory="Current role"),
                _sample_draft(subcategory="References", question_en="Will you provide references?"),
            )
        ],
    )

    result = await gen.generate_whatsapp_questions(job=_build_job())

    assert all(isinstance(q, WhatsAppQuestion) for q in result)

    fixed_part = [q for q in result if not q.is_ai_generated]
    ai_part = [q for q in result if q.is_ai_generated]

    # The fixed set comes first, in canonical order, tagged by source_field.
    assert [q.source_field for q in fixed_part] == list(FIXED_QUESTION_KEYS)
    # None of the fixed questions are AI-scored.
    assert all(q.ai_verifies_response is False for q in fixed_part)

    # AI background questions follow, all AI-scored.
    assert len(ai_part) == 2
    assert all(q.source_field is None for q in ai_part)
    assert all(q.category is QuestionCategory.BACKGROUND_VALIDATION for q in ai_part)
    assert all(q.ai_verifies_response is True for q in ai_part)

    # Orders are contiguous 1..N across the whole list.
    total = N_FIXED + 2
    assert [q.order for q in result] == list(range(1, total + 1))
    assert [q.id for q in result] == [f"q_id{i:06d}" for i in range(1, total + 1)]

    # Fixed categories come from the fixed-question mapping.
    by_field = {q.source_field: q.category for q in fixed_part}
    assert by_field["salary"] is QuestionCategory.SALARY
    assert by_field["visa"] is QuestionCategory.COMMITMENT
    assert by_field["work_mode"] is QuestionCategory.COMMITMENT


async def test_fixed_questions_always_included(
    monkeypatch: pytest.MonkeyPatch, deterministic_ids: None
) -> None:
    """Even with no AI output, the full fixed set appears."""
    _patch_structured(monkeypatch, results=[_wrap()])  # empty AI both calls

    result = await gen.generate_whatsapp_questions(job=_build_job())

    fixed_part = [q for q in result if not q.is_ai_generated]
    assert [q.source_field for q in fixed_part] == list(FIXED_QUESTION_KEYS)
    assert all(q.is_ai_generated is False for q in result)


async def test_fixed_question_fills_placeholders(
    monkeypatch: pytest.MonkeyPatch, deterministic_ids: None
) -> None:
    _patch_structured(monkeypatch, results=[_wrap(_sample_draft())])

    job = _build_job(city="Riyadh", currency="SAR")
    result = await gen.generate_whatsapp_questions(job=job)

    visa_q = next(q for q in result if q.source_field == "visa")
    assert "Riyadh" in visa_q.question_en
    salary_q = next(q for q in result if q.source_field == "salary")
    assert "SAR" in salary_q.question_en


# --- AI draft handling: filter + cap ----------------------------------------


async def test_ai_non_background_dropped_and_capped(
    monkeypatch: pytest.MonkeyPatch,
    deterministic_ids: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    drafts = [
        _sample_draft(subcategory="bg1"),
        _sample_draft(subcategory="bg2"),
        _sample_draft(subcategory="bg3"),
        _sample_draft(subcategory="bg4"),  # 4th background -> over the AI cap
        _sample_draft(category=QuestionCategory.COMMITMENT.value, subcategory="stray"),  # dropped
    ]
    _patch_structured(monkeypatch, results=[_wrap(*drafts)])
    caplog.set_level(logging.WARNING, logger=gen.logger.name)

    result = await gen.generate_whatsapp_questions(job=_build_job())

    ai_part = [q for q in result if q.is_ai_generated]
    assert len(ai_part) == gen.MAX_AI_QUESTIONS
    assert "whatsapp_question_generator.over_cap" in caplog.text
    assert "whatsapp_question_generator.dropped_non_background" in caplog.text


# --- Retry / non-fatal-empty behavior ---------------------------------------


async def test_no_background_in_base_retries_strict(
    monkeypatch: pytest.MonkeyPatch,
    deterministic_ids: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    base_result = _wrap(
        _sample_draft(category=QuestionCategory.COMMITMENT.value, subcategory="stray"),
    )
    strict_result = _wrap(_sample_draft(subcategory="Current role"))

    captured = _patch_structured(monkeypatch, results=[base_result, strict_result])
    caplog.set_level(logging.WARNING, logger=gen.logger.name)

    result = await gen.generate_whatsapp_questions(job=_build_job())

    assert len(captured) == 2
    assert captured[0]["prompt_version"] == gen.PROMPT_VERSION_BASE
    assert captured[0]["prompt"] is gen._PROMPT_BASE
    assert captured[1]["prompt_version"] == gen.PROMPT_VERSION_STRICT
    assert captured[1]["prompt"] is gen._PROMPT_STRICT

    ai_part = [q for q in result if q.is_ai_generated]
    assert len(ai_part) == 1
    assert "no_background_validation_retrying" in caplog.text


async def test_empty_ai_after_retry_is_not_fatal(
    monkeypatch: pytest.MonkeyPatch, deterministic_ids: None
) -> None:
    """No AI background after the retry is fine — fixed questions still stand."""
    captured = _patch_structured(monkeypatch, results=[_wrap()])  # both calls empty

    result = await gen.generate_whatsapp_questions(job=_build_job())

    assert len(captured) == 2  # base + strict retry
    assert all(q.is_ai_generated is False for q in result)
    assert [q.source_field for q in result] == list(FIXED_QUESTION_KEYS)


async def test_background_in_base_skips_retry(
    monkeypatch: pytest.MonkeyPatch, deterministic_ids: None
) -> None:
    captured = _patch_structured(monkeypatch, results=[_wrap(_sample_draft())])
    await gen.generate_whatsapp_questions(job=_build_job())
    assert len(captured) == 1


# --- Error paths ------------------------------------------------------------


async def test_structured_bind_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model response that can't bind to the schema surfaces as the
    service's own error so the pipeline marks the step failed."""

    async def _raise(**_: Any) -> Any:
        raise ClaudeStructuredOutputError("no tool call")

    monkeypatch.setattr(gen, "call_claude_structured", _raise)
    with pytest.raises(gen.WhatsAppQuestionGenerationError, match="schema"):
        await gen.generate_whatsapp_questions(job=_build_job())


# --- Schema validation (the draft contract, tested directly) ----------------


def test_wrapper_requires_questions_key() -> None:
    with pytest.raises(ValidationError):
        WhatsAppQuestionDraftList.model_validate({})


def test_missing_arabic_fails_schema() -> None:
    with pytest.raises(ValidationError):
        WhatsAppQuestionDraftList.model_validate({"questions": [_sample_draft(question_ar="")]})


def test_unknown_category_fails_schema() -> None:
    with pytest.raises(ValidationError):
        WhatsAppQuestionDraftList.model_validate(
            {"questions": [_sample_draft(category="invented_category")]}
        )


def test_removed_category_fails_schema() -> None:
    """The old skill_assessment/logistics/motivation values are gone."""
    with pytest.raises(ValidationError):
        WhatsAppQuestionDraftList.model_validate(
            {"questions": [_sample_draft(category="skill_assessment")]}
        )


def test_extra_keys_rejected_by_schema() -> None:
    # extra='forbid' on the draft schema rejects a leaked id/order.
    with pytest.raises(ValidationError):
        WhatsAppQuestionDraftList.model_validate({"questions": [_sample_draft(id="q_leaked01")]})


# --- structured-call wiring -------------------------------------------------


async def test_prompt_version_and_model_wiring(
    monkeypatch: pytest.MonkeyPatch, deterministic_ids: None
) -> None:
    captured = _patch_structured(monkeypatch, results=[_wrap(_sample_draft())])
    await gen.generate_whatsapp_questions(job=_build_job())

    assert len(captured) == 1
    call = captured[0]
    assert call["prompt_version"] == gen.PROMPT_VERSION_BASE
    assert call["model"] is ClaudeModel.HAIKU
    assert call["schema"] is WhatsAppQuestionDraftList
    assert call["max_tokens"] == gen.MAX_TOKENS
    # The base prompt template (with the base system turn baked in) is used.
    assert call["prompt"] is gen._PROMPT_BASE


async def test_template_context_excludes_verify_flags(
    monkeypatch: pytest.MonkeyPatch, deterministic_ids: None
) -> None:
    """The AI prompt context does NOT consume ai_verify_flags."""
    captured = _patch_structured(monkeypatch, results=[_wrap(_sample_draft())])
    await gen.generate_whatsapp_questions(
        job=_build_job(ai_verify_flags={"salary": True, "notice": True})
    )

    variables = captured[0]["variables"]
    assert "ai_verify_flags" not in variables
    assert "verify_flags" not in variables


async def test_template_context_carries_job_fields(
    monkeypatch: pytest.MonkeyPatch, deterministic_ids: None
) -> None:
    captured = _patch_structured(monkeypatch, results=[_wrap(_sample_draft())])
    await gen.generate_whatsapp_questions(job=_build_job())

    ctx = captured[0]["variables"]
    assert ctx["title"] == "Senior Backend Engineer"
    assert ctx["hiring_company"] == "Kabil Test Corp"
    assert ctx["country"] == "AE"
    assert ctx["city"] == "Dubai"
    assert ctx["min_experience_years"] == 5
    assert ctx["required_skills"] == ["Python", "PostgreSQL"]
    assert ctx["languages_required"] == ["english", "arabic"]
    # Enums are unwrapped to their .value strings.
    assert ctx["employment_type"] == EmploymentType.PERMANENT.value
    assert ctx["work_mode"] == WorkMode.HYBRID.value
    assert ctx["notice_period"] == NoticePeriod.DAYS_30.value
    assert ctx["visa_requirement"] == VisaRequirement.SPONSORSHIP_OFFERED.value


async def test_compensation_omitted_when_unset(
    monkeypatch: pytest.MonkeyPatch, deterministic_ids: None
) -> None:
    captured = _patch_structured(monkeypatch, results=[_wrap(_sample_draft())])
    await gen.generate_whatsapp_questions(job=_build_job(min_salary=None, max_salary=None))
    assert captured[0]["variables"]["compensation"] == "(not specified)"


async def test_compensation_partial_band(
    monkeypatch: pytest.MonkeyPatch, deterministic_ids: None
) -> None:
    captured = _patch_structured(monkeypatch, results=[_wrap(_sample_draft())])
    await gen.generate_whatsapp_questions(job=_build_job(min_salary=15000, max_salary=None))
    assert captured[0]["variables"]["compensation"] == "AED 15000 - ?"


# --- Prompt regression guards -----------------------------------------------


def test_system_prompt_targets_background_validation_only() -> None:
    assert QuestionCategory.BACKGROUND_VALIDATION.value in gen.SYSTEM_PROMPT_BASE
    assert str(gen.MAX_AI_QUESTIONS) in gen.SYSTEM_PROMPT_BASE


def test_strict_prompt_mentions_previous_failure() -> None:
    assert "previous response" in gen.SYSTEM_PROMPT_STRICT
    assert QuestionCategory.BACKGROUND_VALIDATION.value in gen.SYSTEM_PROMPT_STRICT


# --- Real-template smoke test -----------------------------------------------


async def test_real_template_renders_with_job(
    monkeypatch: pytest.MonkeyPatch, deterministic_ids: None
) -> None:
    """Exercise the actual Jinja file via the real prompt template.

    The structured call is stubbed, but we render the captured prompt with the
    captured variables — the regression net for Jinja syntax errors / missing
    context vars in ``generate_whatsapp_questions.j2``.
    """
    captured = _patch_structured(monkeypatch, results=[_wrap(_sample_draft())])
    await gen.generate_whatsapp_questions(job=_build_job())

    call = captured[0]
    rendered = call["prompt"].invoke(call["variables"]).to_messages()[1].content
    assert "Senior Backend Engineer" in rendered
    assert "Kabil Test Corp" in rendered
    assert "Dubai" in rendered
    assert "AED 20000 - 30000" in rendered
    assert "Python, PostgreSQL" in rendered
    assert "Build the Kabil hiring backend" in rendered
