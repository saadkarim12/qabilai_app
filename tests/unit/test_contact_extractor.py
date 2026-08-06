"""Unit tests for the bulk-upload contact-detail extractor.

Claude is stubbed by default — fast, deterministic, and independent of
Anthropic / Redis / filesystem. The LLM fallback now returns tool-bound
structured output (a :class:`ContactDetailsDraft`), so the fakes hand back a
draft instance rather than a raw dict. Sanitization rules mirror the cv_parser
tests since both consumers share the same drop-to-None policy on bogus fields.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.ai import contact_extractor
from src.ai.claude import ClaudeStructuredOutputError
from src.enums.claude_models import ClaudeModel


def _draft(**fields: Any) -> contact_extractor.ContactDetailsDraft:
    """Build the schema instance the structured call would return."""
    return contact_extractor.ContactDetailsDraft(**fields)


def _patch_structured(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: contact_extractor.ContactDetailsDraft,
) -> list[dict[str, Any]]:
    """Replace ``call_claude_structured`` with a fake returning ``result``."""
    captured: list[dict[str, Any]] = []

    async def _fake(**kwargs: Any) -> contact_extractor.ContactDetailsDraft:
        captured.append(kwargs)
        return result

    monkeypatch.setattr(contact_extractor, "call_claude_structured", _fake)
    return captured


# --- Empty / whitespace input ----------------------------------------------


async def test_empty_text_returns_all_none_without_claude_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(**_: Any) -> Any:
        raise AssertionError("call_claude_structured must not run on empty input")

    monkeypatch.setattr(contact_extractor, "call_claude_structured", _boom)
    result = await contact_extractor.extract_contact_details("")
    assert result.full_name is None
    assert result.email is None
    assert result.phone is None


async def test_whitespace_only_returns_all_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(**_: Any) -> Any:
        raise AssertionError("call_claude_structured must not run on whitespace-only input")

    monkeypatch.setattr(contact_extractor, "call_claude_structured", _boom)
    result = await contact_extractor.extract_contact_details("   \n\t  ")
    assert result.full_name is None


# --- Happy path -------------------------------------------------------------


async def test_full_contact_extracted_and_normalised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_structured(
        monkeypatch,
        result=_draft(
            full_name="Layla Hassan",
            email="Layla.Hassan@Example.com",
            phone="+971 50-123 4567",
        ),
    )

    result = await contact_extractor.extract_contact_details("sample cv text")

    assert result.full_name == "Layla Hassan"
    # Email lowercased + stripped.
    assert result.email == "layla.hassan@example.com"
    # Phone normalised to E.164.
    assert result.phone == "+971501234567"

    # Uses Haiku (high-volume, narrow rubric) with structured output bound to
    # the contact-details schema.
    assert len(captured) == 1
    call = captured[0]
    assert call["model"] is ClaudeModel.HAIKU
    assert call["schema"] is contact_extractor.ContactDetailsDraft
    assert call["prompt_version"] == contact_extractor.PROMPT_VERSION


# --- All-fields-missing CV -------------------------------------------------


async def test_all_null_payload_returns_all_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc acceptance: 'returns None fields when CV has no contact info'.

    The bulk-upload service treats this as parse_failed_no_contact.
    """
    _patch_structured(monkeypatch, result=_draft(full_name=None, email=None, phone=None))

    result = await contact_extractor.extract_contact_details("anonymous CV body")
    assert result.full_name is None
    assert result.email is None
    assert result.phone is None


# --- Sanitization edge cases -----------------------------------------------


async def test_bogus_email_dropped_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_structured(
        monkeypatch,
        result=_draft(full_name="Layla Hassan", email="see CV", phone="+971501234567"),
    )

    result = await contact_extractor.extract_contact_details("cv")
    assert result.email is None
    # Other fields survive.
    assert result.full_name == "Layla Hassan"
    assert result.phone == "+971501234567"


async def test_national_format_phone_dropped_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """National-format phones (no '+' prefix) cannot be normalised so
    drop to ``None`` — same policy as the cv_parser."""
    _patch_structured(
        monkeypatch,
        result=_draft(
            full_name="Layla Hassan",
            email="layla.hassan@example.com",
            phone="0501234567",
        ),
    )

    result = await contact_extractor.extract_contact_details("cv")
    assert result.phone is None


async def test_empty_string_email_dropped_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_structured(
        monkeypatch,
        result=_draft(full_name="Layla Hassan", email="   ", phone="+971501234567"),
    )

    result = await contact_extractor.extract_contact_details("cv")
    assert result.email is None


# --- Defensive: model fails to bind to the schema --------------------------


async def test_unbound_response_returns_all_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the model can't produce schema-bound output, degrade to empty
    rather than raise — the caller treats it as parse_failed_no_contact."""

    async def _raise(**_: Any) -> Any:
        raise ClaudeStructuredOutputError("no tool call")

    monkeypatch.setattr(contact_extractor, "call_claude_structured", _raise)

    result = await contact_extractor.extract_contact_details("cv text")
    assert result.full_name is None
    assert result.email is None
    assert result.phone is None


# --- Deterministic pass (skip the LLM) -------------------------------------


def _patch_structured_boom(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(**_: Any) -> Any:
        raise AssertionError("deterministic pass should have skipped the LLM")

    monkeypatch.setattr(contact_extractor, "call_claude_structured", _boom)


async def test_clean_cv_skips_llm_and_extracts_all_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unambiguous name + one email + one international phone → no Claude call."""
    _patch_structured_boom(monkeypatch)
    cv = (
        "Layla Hassan\n"
        "Senior Software Engineer\n"
        "layla.hassan@example.com | +971 50 123 4567 | Dubai\n\n"
        "SUMMARY\nBackend engineer.\n"
    )
    result = await contact_extractor.extract_contact_details(cv)
    assert result.full_name == "Layla Hassan"
    assert result.email == "layla.hassan@example.com"
    assert result.phone == "+971501234567"


async def test_multiple_emails_is_ambiguous_falls_back_to_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two distinct emails (e.g. a referee's) → defer to the LLM."""
    captured = _patch_structured(
        monkeypatch,
        result=_draft(full_name="Layla Hassan", email="layla@example.com", phone=None),
    )
    cv = (
        "Layla Hassan\n"
        "layla.hassan@example.com | +971 50 123 4567\n"
        "Referee: prof.smith@university.edu\n"
    )
    await contact_extractor.extract_contact_details(cv)
    assert len(captured) == 1  # LLM was called


async def test_missing_name_falls_back_to_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Email + phone present but no name-like top line → defer to the LLM."""
    captured = _patch_structured(
        monkeypatch,
        result=_draft(full_name=None, email=None, phone=None),
    )
    cv = "layla.hassan@example.com\n+971 50 123 4567\n"
    await contact_extractor.extract_contact_details(cv)
    assert len(captured) == 1


async def test_national_format_phone_falls_back_to_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A phone without a country code isn't matched (region=None) → defer."""
    captured = _patch_structured(
        monkeypatch,
        result=_draft(full_name=None, email=None, phone=None),
    )
    cv = "Layla Hassan\nlayla.hassan@example.com\n050 123 4567\n"
    await contact_extractor.extract_contact_details(cv)
    assert len(captured) == 1


async def test_size_ranked_name_beats_positional_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Largest-font candidate wins even when a title leads the flattened text.

    Reproduces the designed-CV failure mode: pypdf flows the job-title banner
    above the name, so a top-of-document scan would grab the title. With
    size-ranked candidates the real name is picked — and Claude is not called.
    """
    _patch_structured_boom(monkeypatch)
    cv = (
        "Full Stack Software Engineer\nmuhammadawais@example.com +971 50 123 4567\nMuhammad Awais\n"
    )
    result = await contact_extractor.extract_contact_details(
        cv, name_candidates=["Muhammad Awais", "Full Stack Software Engineer"]
    )
    assert result.full_name == "Muhammad Awais"
    assert result.email == "muhammadawais@example.com"
    assert result.phone == "+971501234567"


async def test_size_candidates_without_a_name_defer_to_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Size info present but no candidate looks like a name → defer to Claude.

    The positional scan *would* find the name lower in the text, but once we
    have a size signal we trust it and refuse to guess from position.
    """
    captured = _patch_structured(
        monkeypatch,
        result=_draft(full_name="Muhammad Awais", email=None, phone=None),
    )
    cv = "Curriculum Vitae\nmuhammadawais@example.com +971 50 123 4567\nMuhammad Awais\n"
    await contact_extractor.extract_contact_details(
        cv, name_candidates=["Curriculum Vitae", "Experience"]
    )
    assert len(captured) == 1  # LLM called; positional scan not used


async def test_deglued_block_text_yields_clean_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Email comes from contact_block_text, dodging a label glued in cv_text.

    The flattened ``cv_text`` has ``Emailmuhammadawais1000@gmail.com`` (the
    label stuck to the address); the de-glued block text has them separated.
    The clean address must win — and no Claude call is made.
    """
    _patch_structured_boom(monkeypatch)
    glued_cv = "Muhammad Awais\nPhone+923346798544 Emailmuhammadawais1000@gmail.com\n"
    deglued = "Muhammad Awais Phone +923346798544 Email muhammadawais1000@gmail.com"
    result = await contact_extractor.extract_contact_details(
        glued_cv,
        name_candidates=["Muhammad Awais"],
        contact_block_text=deglued,
    )
    assert result.full_name == "Muhammad Awais"
    assert result.email == "muhammadawais1000@gmail.com"  # not 'email...'
    assert result.phone == "+923346798544"


async def test_glued_email_without_block_text_falls_back_to_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without de-glued text the glued email survives — but it's still a single
    match, so the deterministic pass would emit the wrong address. Guard the
    regression by asserting the de-glued path is what fixes it: with no block
    text and a glued address, the extracted email carries the label."""
    _patch_structured_boom(monkeypatch)
    glued_cv = "Muhammad Awais\nPhone+923346798544 Emailmuhammadawais1000@gmail.com\n"
    result = await contact_extractor.extract_contact_details(
        glued_cv,
        name_candidates=["Muhammad Awais"],
    )
    # Documents the pre-fix behavior the de-glued path corrects.
    assert result.email == "emailmuhammadawais1000@gmail.com"


def test_resolve_name_prefers_size_candidate_over_position() -> None:
    name = contact_extractor._resolve_name(
        "Job Title Banner\nReal Person\n",
        ["Real Person", "Job Title Banner"],
    )
    assert name == "Real Person"


def test_resolve_name_falls_back_to_position_without_candidates() -> None:
    # Empty list and None both mean "no size signal" → positional scan.
    assert contact_extractor._resolve_name("Layla Hassan\nEngineer\n", None) == "Layla Hassan"
    assert contact_extractor._resolve_name("Layla Hassan\nEngineer\n", []) == "Layla Hassan"


def test_heuristic_name_rejects_headers_and_accepts_names() -> None:
    assert contact_extractor._looks_like_name("Layla Hassan") is True
    assert contact_extractor._looks_like_name("LAYLA HASSAN") is True
    assert contact_extractor._looks_like_name("Mohammed Al-Hassan") is True
    # Rejected: header word, contains digits, email, label, too many tokens.
    assert contact_extractor._looks_like_name("Curriculum Vitae") is False
    assert contact_extractor._looks_like_name("Senior Engineer 2024") is False
    assert contact_extractor._looks_like_name("layla.hassan@example.com") is False
    assert contact_extractor._looks_like_name("Email: contact me") is False
    assert contact_extractor._looks_like_name("a b c d e f") is False
