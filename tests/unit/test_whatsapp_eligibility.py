"""Unit tests for the WhatsApp eligibility extractor (Step 5.5).

Covers the closed-set invariant (extractable keys are a subset of the fixed
questions, minus commitment), the schema's ``extra='forbid'`` guard, and the
extractor's best-effort short-circuits (blank answer / bind failure → ``None``)
without touching Anthropic.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.ai.claude import ClaudeStructuredOutputError
from src.enums.fixed_screening_questions import FIXED_QUESTION_KEYS
from src.enums.whatsapp_eligibility import EXTRACTABLE_FIXED_KEYS, EligibilityKey
from src.schemas.whatsapp_eligibility import NOTICE_PERIOD_DAYS_MAX, EligibilityExtraction
from src.services import whatsapp_eligibility_extractor as extractor


def test_extractable_keys_are_a_subset_of_fixed_questions() -> None:
    """Every extractable key is a real fixed-question source_field."""
    assert EXTRACTABLE_FIXED_KEYS <= set(FIXED_QUESTION_KEYS)


def test_commitment_is_not_extracted() -> None:
    """Commitment is derived from state, not parsed — it must stay out of the set."""
    assert "commitment" in FIXED_QUESTION_KEYS
    assert "commitment" not in EXTRACTABLE_FIXED_KEYS
    assert {k.value for k in EligibilityKey} == EXTRACTABLE_FIXED_KEYS


def test_schema_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        EligibilityExtraction.model_validate({"salary_amount": 15_800, "bogus": 1})


def test_schema_all_fields_optional() -> None:
    """An empty extraction is valid (the model fills only the asked field)."""
    ex = EligibilityExtraction()
    assert ex.salary_amount is None
    assert ex.accepted is None


def test_in_range_notice_period_is_preserved() -> None:
    """A plausible notice period binds and is kept verbatim."""
    assert EligibilityExtraction(notice_period_days=90).notice_period_days == 90
    assert EligibilityExtraction(notice_period_days=NOTICE_PERIOD_DAYS_MAX).notice_period_days == (
        NOTICE_PERIOD_DAYS_MAX
    )


def test_implausible_notice_period_drops_to_null_instead_of_failing_bind() -> None:
    """An out-of-range notice period (e.g. "10000 days") must not fail the whole
    bind — it is dropped to null so every other extracted field survives."""
    ex = EligibilityExtraction(notice_period_days=10_000, salary_amount=15_800)
    assert ex.notice_period_days is None
    assert ex.salary_amount == 15_800  # co-extracted field is unaffected

    assert EligibilityExtraction(notice_period_days=-1).notice_period_days is None


def test_missing_value_gets_a_short_not_stated_note() -> None:
    """A null primary value carries a short human-readable note for HR, not a
    silent blank — covers evasive answers (visa) and implausible ones (notice)."""
    from src.services.whatsapp_conversation_service import _normalize_extraction

    visa = _normalize_extraction(EligibilityKey.VISA, None, EligibilityExtraction(visa_valid=None))
    assert visa == {"valid": None, "note": "No visa status stated"}

    # 10000 days is dropped to null by the schema validator, then noted here.
    notice = _normalize_extraction(
        EligibilityKey.NOTICE_PERIOD, None, EligibilityExtraction(notice_period_days=10_000)
    )
    assert notice == {"days": None, "note": "No notice period stated"}


def test_present_value_has_no_note() -> None:
    """A determined value renders as-is, with no note key."""
    from src.services.whatsapp_conversation_service import _normalize_extraction

    out = _normalize_extraction(
        EligibilityKey.NOTICE_PERIOD, None, EligibilityExtraction(notice_period_days=90)
    )
    assert out == {"days": 90}
    assert "note" not in out


@pytest.mark.asyncio
async def test_blank_answer_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blank answer returns None without calling Claude."""
    called = False

    async def _boom(*args: object, **kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("Claude must not be called for a blank answer")

    monkeypatch.setattr(extractor, "call_claude_structured", _boom)
    result = await extractor.extract_fixed_answer(
        source_field="salary", question="Expected salary?", answer="   "
    )
    assert result is None
    assert called is False


@pytest.mark.asyncio
async def test_bind_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A structured-bind failure is swallowed into None (never breaks the flow)."""

    async def _fail(*args: object, **kwargs: object) -> object:
        raise ClaudeStructuredOutputError("bad output")

    monkeypatch.setattr(extractor, "call_claude_structured", _fail)
    result = await extractor.extract_fixed_answer(
        source_field="visa", question="Visa status?", answer="employment visa, transferable"
    )
    assert result is None


@pytest.mark.asyncio
async def test_successful_extraction_returns_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok(*args: object, **kwargs: object) -> EligibilityExtraction:
        return EligibilityExtraction(notice_period_days=14)

    monkeypatch.setattr(extractor, "call_claude_structured", _ok)
    result = await extractor.extract_fixed_answer(
        source_field="notice_period", question="Notice period?", answer="two weeks"
    )
    assert result is not None
    assert result.notice_period_days == 14
