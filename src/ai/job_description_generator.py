"""AI job-description builder.

Given a :class:`JobDescriptionGenerateRequest` (the Role-Basics spec the HR user
filled on step 1 of the create-job wizard), drafts a complete, ready-to-post job
description with Claude (Sonnet — the default for routine drafting/rephrasing).

The draft is **bilingual**: Claude returns a native English JD and a native
Gulf-Arabic JD (not a machine translation) via tool-forced structured output
(:class:`JdDraft`), so both fields are guaranteed parseable every time.

This runs *synchronously inside the request* — unlike the WhatsApp-question
generator, there is no Job row yet (the wizard calls this on the JD Builder step,
before the job is created), so there's nothing to attach a Celery
``pipeline_status`` to. A single interactive Claude call of a few seconds is the
right shape.

Caching is delegated to :func:`call_claude_structured`: an identical spec re-pays
nothing on the second call. A ``regenerate`` call deliberately bypasses the cache
and lifts the temperature so HR gets a genuinely different draft on each click.
"""

from __future__ import annotations

import logging
from typing import Any, Final

import anthropic

from src.ai.claude import (
    AnthropicKeyMissingError,
    ClaudeStructuredOutputError,
    call_claude_structured,
    load_chat_prompt,
)
from src.enums.claude_models import ClaudeModel
from src.enums.prompts import JD_BUILDER_SYSTEM_PROMPT
from src.exceptions import AiServiceUnavailableError, JobDescriptionGenerationError
from src.schemas.job import JdDraft, JobDescriptionGenerateRequest
from src.utils.countries import country_display_name

logger = logging.getLogger(__name__)

# --- Constants --------------------------------------------------------------

# Jinja template that renders the user-message half of the prompt.
PROMPT_TEMPLATE: Final[str] = "generate_job_description.j2"

# Chat prompt (system turn + rendered human template) for the structured call.
# Built once at import; the human template's variables are supplied per call.
JD_PROMPT: Final = load_chat_prompt(
    system=JD_BUILDER_SYSTEM_PROMPT,
    human_template_file=PROMPT_TEMPLATE,
)

# Cache-key tag. Bump the version segment when the template or system prompt
# semantics change so stale drafts aren't served. v2 = bilingual (EN + AR),
# tool-forced structured output.
PROMPT_VERSION: Final[str] = "generate_job_description:base:v2"

# Two 350-500 word JDs (English + Arabic) fit comfortably; headroom for the
# bullet structure and the wider token cost of Arabic script.
MAX_TOKENS: Final[int] = 3000

# Temperature for a "regenerate" click — enough variation to feel fresh while
# staying on-spec. Paired with ``use_cache=False`` so each click actually
# re-rolls (a cached deterministic call would return identical text).
REGENERATE_TEMPERATURE: Final[float] = 0.7

# Reject an obviously-truncated/empty draft. Mirrors the frontend's 100-char
# minimum on the description field so the builder never hands back something the
# wizard would immediately flag as invalid.
MIN_GENERATED_LENGTH: Final[int] = 100

# Anthropic exceptions worth surfacing as a retriable upstream failure (502)
# rather than a 500. Same set the job pipeline treats as transient.
_TRANSIENT_ANTHROPIC: Final[tuple[type[Exception], ...]] = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)

# Failures surfaced to the caller as a retriable 502: a transient Anthropic
# error, or a structured-output that couldn't be bound to :class:`JdDraft`.
_GENERATION_ERRORS: Final[tuple[type[Exception], ...]] = (
    ClaudeStructuredOutputError,
    *_TRANSIENT_ANTHROPIC,
)


def _compensation_line(spec: JobDescriptionGenerateRequest) -> str:
    """Format the salary band for the prompt, hiding it entirely when unset."""
    if spec.min_salary is None and spec.max_salary is None:
        return "(not specified)"
    low: int | str = spec.min_salary if spec.min_salary is not None else "?"
    high: int | str = spec.max_salary if spec.max_salary is not None else "?"
    return f"{spec.currency} {low} - {high} per month"


def _build_template_context(spec: JobDescriptionGenerateRequest) -> dict[str, Any]:
    """Pull exactly the fields the prompt needs from the request schema."""
    return {
        "title": spec.title,
        "hiring_company": spec.hiring_company,
        "country_display": country_display_name(spec.country),
        "city": spec.city,
        "employment_type": spec.employment_type.value,
        "work_mode": spec.work_mode.value,
        "compensation": _compensation_line(spec),
        "notice_period": spec.notice_period.value if spec.notice_period else None,
        "min_experience_years": spec.min_experience_years,
        "required_skills": list(spec.required_skills),
        "preferred_skills": list(spec.preferred_skills),
        "visa_requirement": spec.visa_requirement.value if spec.visa_requirement else None,
        "nationality_preference": list(spec.nationality_preference),
        "languages_required": list(spec.languages_required),
    }


async def generate_job_description(
    *,
    spec: JobDescriptionGenerateRequest,
    regenerate: bool = False,
) -> JdDraft:
    """Draft a bilingual job description from a Role-Basics spec.

    Args:
        spec: The validated Role-Basics fields from the create-job wizard.
        regenerate: When ``True`` bypass the Claude cache and raise the
            temperature so a repeat call yields a fresh draft.

    Returns:
        A :class:`JdDraft` carrying the stripped English (``job_description_en``)
        and native Gulf-Arabic (``job_description_ar``) drafts, each guaranteed
        at least :data:`MIN_GENERATED_LENGTH` characters.

    Raises:
        AiServiceUnavailableError: ``ANTHROPIC_API_KEY`` is not configured (503).
        JobDescriptionGenerationError: a transient Claude failure, a structured
            output that couldn't be bound, or an unusably short draft (502).
    """
    try:
        draft = await call_claude_structured(
            model=ClaudeModel.SONNET,
            prompt=JD_PROMPT,
            variables=_build_template_context(spec),
            schema=JdDraft,
            max_tokens=MAX_TOKENS,
            prompt_version=PROMPT_VERSION,
            temperature=REGENERATE_TEMPERATURE if regenerate else 0.0,
            use_cache=not regenerate,
        )
    except AnthropicKeyMissingError as exc:
        raise AiServiceUnavailableError(
            "The AI job-description builder is unavailable: Claude is not configured."
        ) from exc
    except _GENERATION_ERRORS as exc:
        raise JobDescriptionGenerationError(
            "The AI couldn't draft a description right now. Please try again."
        ) from exc

    result = JdDraft(
        job_description_en=draft.job_description_en.strip(),
        job_description_ar=draft.job_description_ar.strip(),
    )
    shortest = min(len(result.job_description_en), len(result.job_description_ar))
    if shortest < MIN_GENERATED_LENGTH:
        logger.warning(
            "job_description_generator.too_short",
            extra={
                "en_length": len(result.job_description_en),
                "ar_length": len(result.job_description_ar),
            },
        )
        raise JobDescriptionGenerationError(
            "The AI returned an unusably short description. Please try again."
        )

    logger.info(
        "job_description_generator.ok",
        extra={
            "en_length": len(result.job_description_en),
            "ar_length": len(result.job_description_ar),
        },
    )
    return result
