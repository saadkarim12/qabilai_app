"""Unit tests for the CV parser.

The structured Claude call is stubbed by default — fast, deterministic, and
independent of Anthropic / Redis / filesystem. A success outcome hands back a
validated ``ParsedCVDraft``; a failure outcome raises
``ClaudeStructuredOutputError`` carrying the model's raw tool args (what the
salvage path rescues from). One test (``test_real_template_renders``) renders
the real ``parse_cv.j2`` to catch Jinja regressions.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.ai import cv_parser
from src.ai.claude import ClaudeStructuredOutputError
from src.enums.claude_models import ClaudeModel
from src.enums.language import LanguageCode
from src.enums.parsed_cv import ParseStatus
from src.schemas.parsed_cv import ParsedCVDraft

# --- Helpers ----------------------------------------------------------------


def _good_draft(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "full_name": "Layla Hassan",
        "email": "layla.hassan@example.com",
        "phone": "+971501234567",
        "skills": ["Python", "PostgreSQL", "FastAPI"],
        "work_history": [
            {
                "company": "Acme Corp",
                "title": "Senior Backend Engineer",
                "start": "Jan 2020",
                "end": None,
                "description": "Led the migration to async stack.",
            }
        ],
        "education": [
            {
                "institution": "American University of Sharjah",
                "degree": "B.Sc.",
                "field": "Computer Science",
                "start": "2014",
                "end": "2018",
            }
        ],
        "total_experience_years": 6.5,
        "languages": ["English", "Arabic"],
    }
    base.update(overrides)
    return base


def _draft(**overrides: Any) -> ParsedCVDraft:
    """A validated draft — what a successful structured call returns."""
    return ParsedCVDraft.model_validate(_good_draft(**overrides))


def _fail(raw_args: dict[str, Any]) -> ClaudeStructuredOutputError:
    """A bind-failure outcome carrying the model's raw tool args for salvage."""
    return ClaudeStructuredOutputError("bind failed", raw_args=raw_args)


def _patch_structured(
    monkeypatch: pytest.MonkeyPatch,
    *,
    outcomes: list[Any],
) -> list[dict[str, Any]]:
    """Replace ``call_claude_structured`` with successive ``outcomes``.

    Each entry is either a ``ParsedCVDraft`` (returned) or a
    ``ClaudeStructuredOutputError`` (raised). Captures kwargs for wiring
    assertions.
    """
    captured: list[dict[str, Any]] = []
    pending = list(outcomes)

    async def _fake(**kwargs: Any) -> ParsedCVDraft:
        captured.append(kwargs)
        if not pending:
            raise AssertionError("call_claude_structured called more times than outcomes provided")
        item = pending.pop(0)
        if isinstance(item, ClaudeStructuredOutputError):
            raise item
        return item

    monkeypatch.setattr(cv_parser, "call_claude_structured", _fake)
    return captured


# --- Empty input -----------------------------------------------------------


async def test_empty_text_returns_empty_parsed_cv_without_claude_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc acceptance: 'Empty CV produces empty fields, not crash'."""

    async def _boom(**_: Any) -> Any:
        raise AssertionError("call_claude_structured must not run on empty input")

    monkeypatch.setattr(cv_parser, "call_claude_structured", _boom)
    result = await cv_parser.parse_cv("", LanguageCode.EN)
    assert result.parse_status is ParseStatus.OK
    assert result.full_name is None
    assert result.skills == []
    assert result.work_history == []


async def test_whitespace_only_returns_empty_parsed_cv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(**_: Any) -> Any:
        raise AssertionError("call_claude_structured must not run on whitespace-only input")

    monkeypatch.setattr(cv_parser, "call_claude_structured", _boom)
    result = await cv_parser.parse_cv("   \n\t  ", LanguageCode.EN)
    assert result.parse_status is ParseStatus.OK
    assert result.full_name is None


# --- Happy path -----------------------------------------------------------


async def test_valid_response_returns_parsed_cv_status_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc acceptance: 'Sample CV produces non-empty skills array + experience years'."""
    captured = _patch_structured(monkeypatch, outcomes=[_draft()])

    result = await cv_parser.parse_cv("Some CV text here.", LanguageCode.EN)

    assert result.parse_status is ParseStatus.OK
    assert result.full_name == "Layla Hassan"
    assert result.email == "layla.hassan@example.com"
    assert result.phone == "+971501234567"
    assert "Python" in result.skills
    assert result.total_experience_years == 6.5
    assert len(result.work_history) == 1
    assert result.work_history[0].company == "Acme Corp"
    assert result.languages == ["English", "Arabic"]
    # Sonnet, schema-bound, base prompt on the happy path.
    assert len(captured) == 1
    assert captured[0]["model"] is ClaudeModel.SONNET
    assert captured[0]["schema"] is ParsedCVDraft
    assert captured[0]["prompt_version"] == cv_parser.PROMPT_VERSION_BASE
    assert captured[0]["prompt"] is cv_parser._PROMPT_BASE


# --- Email / phone sanitization -------------------------------------------


async def test_invalid_email_dropped_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_structured(monkeypatch, outcomes=[_draft(email="see CV")])

    result = await cv_parser.parse_cv("text", LanguageCode.EN)
    assert result.email is None
    # Rest of the parse still survives.
    assert result.full_name == "Layla Hassan"
    assert result.parse_status is ParseStatus.OK


async def test_valid_phone_normalized_to_e164(monkeypatch: pytest.MonkeyPatch) -> None:
    # International format with formatting — should normalize cleanly.
    _patch_structured(monkeypatch, outcomes=[_draft(phone="+971 50 123 4567")])

    result = await cv_parser.parse_cv("text", LanguageCode.EN)
    assert result.phone == "+971501234567"


async def test_national_format_phone_dropped_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # A bare national format (no '+' prefix) is dropped to None — per the doc,
    # "must normalize to E.164 or null". The candidate still lands in the system.
    _patch_structured(monkeypatch, outcomes=[_draft(phone="050 123 4567")])

    result = await cv_parser.parse_cv("text", LanguageCode.EN)
    assert result.phone is None


async def test_invalid_phone_dropped_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_structured(monkeypatch, outcomes=[_draft(phone="N/A")])

    result = await cv_parser.parse_cv("text", LanguageCode.EN)
    assert result.phone is None


# --- Retry / strict prompt ------------------------------------------------


async def test_invalid_response_triggers_retry_with_strict_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc acceptance: 'Malformed Claude output triggers retry'."""
    # First attempt can't bind; second binds cleanly.
    captured = _patch_structured(
        monkeypatch,
        outcomes=[_fail(_good_draft(invented_key="leak")), _draft()],
    )

    result = await cv_parser.parse_cv("text", LanguageCode.EN)

    assert result.parse_status is ParseStatus.OK
    # Both prompts were called, with the two different prompt_versions / templates.
    assert len(captured) == 2
    versions = [call["prompt_version"] for call in captured]
    assert versions == [cv_parser.PROMPT_VERSION_BASE, cv_parser.PROMPT_VERSION_STRICT]
    prompts = [call["prompt"] for call in captured]
    assert prompts == [cv_parser._PROMPT_BASE, cv_parser._PROMPT_STRICT]


# --- Second-failure salvage path ------------------------------------------


async def test_second_failure_salvages_and_flags_needs_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Both attempts fail to bind (extra key), but the raw args carry values the
    # salvage path can rescue.
    bad_args = {
        "full_name": "Layla Hassan",
        "email": "layla@example.com",
        "phone": "+971501234567",
        "skills": ["Python"],
        "work_history": [],
        "education": [],
        "total_experience_years": 6.5,
        "languages": ["English"],
        "invented_key": "rejected by extra='forbid'",
    }
    _patch_structured(monkeypatch, outcomes=[_fail(bad_args), _fail(bad_args)])

    result = await cv_parser.parse_cv("text", LanguageCode.EN)

    assert result.parse_status is ParseStatus.NEEDS_REVIEW
    # Validated subset survived.
    assert result.full_name == "Layla Hassan"
    assert result.email == "layla@example.com"
    assert result.phone == "+971501234567"
    assert result.skills == ["Python"]
    assert result.total_experience_years == 6.5
    assert result.languages == ["English"]


async def test_salvage_drops_malformed_work_entries_keeps_good_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_args = {
        "full_name": "Layla Hassan",
        "skills": ["Python"],
        "work_history": [
            # Good entry.
            {"company": "Acme", "title": "Eng", "start": "2020", "end": None, "description": None},
            # Bad: missing required ``title``.
            {"company": "Beta Inc", "start": "2018"},
            # Bad: wrong type.
            "not a dict",
        ],
        "education": [],
        "languages": [],
        "total_experience_years": 4.0,
        "email": None,
        "phone": None,
        "invented_key": "trip extra='forbid'",
    }
    _patch_structured(monkeypatch, outcomes=[_fail(bad_args), _fail(bad_args)])

    result = await cv_parser.parse_cv("text", LanguageCode.EN)

    assert result.parse_status is ParseStatus.NEEDS_REVIEW
    assert len(result.work_history) == 1
    assert result.work_history[0].company == "Acme"


async def test_salvage_drops_out_of_range_experience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 2015 is a year, not a years-of-experience number — must be dropped.
    bad_args = {
        "full_name": "X",
        "skills": [],
        "work_history": [],
        "education": [],
        "languages": [],
        "total_experience_years": 2015,
        "email": None,
        "phone": None,
        "invented_key": "x",
    }
    _patch_structured(monkeypatch, outcomes=[_fail(bad_args), _fail(bad_args)])

    result = await cv_parser.parse_cv("text", LanguageCode.EN)
    assert result.parse_status is ParseStatus.NEEDS_REVIEW
    assert result.total_experience_years is None


async def test_salvage_drops_non_string_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    bad_args = {
        "full_name": "X",
        "skills": ["Python", 42, None, "AWS"],
        "work_history": [],
        "education": [],
        "languages": [],
        "total_experience_years": 3.0,
        "email": None,
        "phone": None,
        "invented_key": "x",
    }
    _patch_structured(monkeypatch, outcomes=[_fail(bad_args), _fail(bad_args)])

    result = await cv_parser.parse_cv("text", LanguageCode.EN)
    assert result.parse_status is ParseStatus.NEEDS_REVIEW
    assert result.skills == ["Python", "AWS"]


# --- Schema strictness ----------------------------------------------------


async def test_extra_keys_from_claude_trigger_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An extra key fails the schema bind on the first attempt → retry."""
    bad = _good_draft()
    bad["confidence"] = 0.99  # invented key — not in schema
    captured = _patch_structured(monkeypatch, outcomes=[_fail(bad), _draft()])

    result = await cv_parser.parse_cv("text", LanguageCode.EN)

    assert result.parse_status is ParseStatus.OK
    assert len(captured) == 2  # retry happened


async def test_unbindable_response_triggers_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    # A response the model couldn't bind (no tool args) shouldn't crash — retry.
    captured = _patch_structured(monkeypatch, outcomes=[_fail({}), _draft()])

    result = await cv_parser.parse_cv("text", LanguageCode.EN)
    assert result.parse_status is ParseStatus.OK
    assert len(captured) == 2


# --- Constants pinned -----------------------------------------------------


def test_constants_pinned() -> None:
    assert cv_parser.PROMPT_TEMPLATE == "parse_cv.j2"
    assert cv_parser.PROMPT_VERSION_BASE != cv_parser.PROMPT_VERSION_STRICT
    assert cv_parser.PROMPT_VERSION_BASE.startswith("parse_cv:base:")
    assert cv_parser.PROMPT_VERSION_STRICT.startswith("parse_cv:strict:")
    assert cv_parser.MAX_TOKENS >= 2048
    assert cv_parser.MAX_INPUT_CHARS == 50_000


# --- Template wiring ------------------------------------------------------


async def test_real_template_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render the captured prompt — catches Jinja syntax errors in parse_cv.j2."""
    captured = _patch_structured(monkeypatch, outcomes=[_draft()])

    result = await cv_parser.parse_cv("Some CV text here.", LanguageCode.AR)
    assert result.parse_status is ParseStatus.OK

    call = captured[0]
    rendered = call["prompt"].invoke(call["variables"]).to_messages()[1].content
    assert "Some CV text here." in rendered
    assert LanguageCode.AR.value in rendered
