"""Unit tests for the AI job-description generator.

The Claude call is stubbed by default — fast, deterministic, and independent of
Anthropic / Redis. One test renders the real ``generate_job_description.j2`` so
Jinja regressions are caught.
"""

from __future__ import annotations

from typing import Any

import anthropic
import httpx
import pytest

from src.ai import job_description_generator as gen
from src.ai.claude import AnthropicKeyMissingError, ClaudeStructuredOutputError
from src.enums.claude_models import ClaudeModel
from src.exceptions import AiServiceUnavailableError, JobDescriptionGenerationError
from src.schemas.job import JdDraft, JobDescriptionGenerateRequest

# --- Helpers ----------------------------------------------------------------


def _spec(**overrides: Any) -> JobDescriptionGenerateRequest:
    base: dict[str, Any] = {
        "title": "Senior Backend Engineer",
        "hiring_company": "Kabil Test Co",
        "country": "AE",
        "city": "Dubai",
        "employment_type": "permanent",
        "work_mode": "hybrid",
        "currency": "AED",
        "min_salary": 20_000,
        "max_salary": 30_000,
        "notice_period": "30d",
        "min_experience_years": 5,
        "required_skills": ["Python", "PostgreSQL"],
        "preferred_skills": ["AWS"],
        "visa_requirement": "sponsorship_offered",
        "nationality_preference": [],
        "languages_required": ["English"],
    }
    base.update(overrides)
    return JobDescriptionGenerateRequest.model_validate(base)


_LONG_EN = ("Role Overview\n" + "We are hiring a strong engineer. " * 20).strip()
_LONG_AR = ("نظرة عامة عن الدور\n" + "نبحث عن مهندس متمكن. " * 20).strip()


def _draft(en: str = _LONG_EN, ar: str = _LONG_AR) -> JdDraft:
    return JdDraft(job_description_en=en, job_description_ar=ar)


def _transient_anthropic() -> anthropic.APITimeoutError:
    return anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com"))


# --- Tests ------------------------------------------------------------------


async def test_returns_stripped_bilingual_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_call(**kwargs: Any) -> JdDraft:
        captured.update(kwargs)
        return _draft(en=f"  {_LONG_EN}  ", ar=f"  {_LONG_AR}  ")

    monkeypatch.setattr(gen, "call_claude_structured", fake_call)

    draft = await gen.generate_job_description(spec=_spec())

    assert draft.job_description_en == _LONG_EN  # stripped
    assert draft.job_description_ar == _LONG_AR  # stripped
    assert captured["model"] is ClaudeModel.SONNET
    assert captured["schema"] is JdDraft
    assert captured["use_cache"] is True
    assert captured["temperature"] == 0.0


async def test_regenerate_bypasses_cache_and_lifts_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_call(**kwargs: Any) -> JdDraft:
        captured.update(kwargs)
        return _draft()

    monkeypatch.setattr(gen, "call_claude_structured", fake_call)

    await gen.generate_job_description(spec=_spec(), regenerate=True)

    assert captured["use_cache"] is False
    assert captured["temperature"] == gen.REGENERATE_TEMPERATURE


async def test_too_short_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_call(**_kwargs: Any) -> JdDraft:
        return _draft(en="too short", ar=_LONG_AR)

    monkeypatch.setattr(gen, "call_claude_structured", fake_call)

    with pytest.raises(JobDescriptionGenerationError):
        await gen.generate_job_description(spec=_spec())


async def test_missing_key_maps_to_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call(**_kwargs: Any) -> JdDraft:
        raise AnthropicKeyMissingError("no key")

    monkeypatch.setattr(gen, "call_claude_structured", fake_call)

    with pytest.raises(AiServiceUnavailableError):
        await gen.generate_job_description(spec=_spec())


async def test_transient_anthropic_maps_to_generation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call(**_kwargs: Any) -> JdDraft:
        raise _transient_anthropic()

    monkeypatch.setattr(gen, "call_claude_structured", fake_call)

    with pytest.raises(JobDescriptionGenerationError):
        await gen.generate_job_description(spec=_spec())


async def test_structured_bind_failure_maps_to_generation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call(**_kwargs: Any) -> JdDraft:
        raise ClaudeStructuredOutputError("could not bind")

    monkeypatch.setattr(gen, "call_claude_structured", fake_call)

    with pytest.raises(JobDescriptionGenerationError):
        await gen.generate_job_description(spec=_spec())


def test_real_template_renders_with_spec() -> None:
    """The real chat prompt renders without error and includes the spec."""
    messages = gen.JD_PROMPT.invoke(gen._build_template_context(_spec())).to_messages()
    human = messages[-1].content
    assert "Senior Backend Engineer" in human
    assert "Python" in human
    assert "AED 20000 - 30000 per month" in human
    assert "United Arab Emirates" in human  # country display name, not "AE"


def test_compensation_line_hidden_when_unset() -> None:
    line = gen._compensation_line(_spec(min_salary=None, max_salary=None))
    assert line == "(not specified)"
