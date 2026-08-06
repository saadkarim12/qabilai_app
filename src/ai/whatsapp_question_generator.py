"""WhatsApp screening-question generator.

Given a :class:`Job`, builds its screening-question list from two sources:

* **Fixed** — the deterministic question set from
  :mod:`src.enums.fixed_screening_questions`, identical for every job (only the
  ``{placeholder}`` tokens differ). These cover the candidate-facing
  ``COMMITMENT`` / ``SALARY`` topics and the fixed visa
  ``BACKGROUND_VALIDATION`` question; they carry ``is_ai_generated=False``,
  ``source_field=<template key>``, and ``ai_verifies_response=False``.
* **AI-authored** — up to ``WHATSAPP_MAX_AI_QUESTIONS`` ``BACKGROUND_VALIDATION``
  questions from Claude (Haiku) that verify the candidate's hands-on experience
  with the role's listed required + preferred skills (skills only — no work
  history), appended after the fixed questions. These carry
  ``ai_verifies_response=True`` and are the only questions whose answers are
  AI-scored downstream.

The service is the *only* place that mints question IDs and assigns
``order`` — Claude returns content only. This keeps IDs stable (no
hallucinated duplicates) and lets us trust the schema downstream.

Caching is delegated to :func:`call_claude_structured`: the cache key already
covers the rendered prompt, model, schema, and ``prompt_version``, so an
identical Job re-pays nothing on the second call.

The final list always contains the fixed set, so an empty AI response is fine.
If the base AI prompt yields no usable background_validation item, the
generator retries **once** with a stricter system prompt before giving up on AI
questions (without raising). A :class:`WhatsAppQuestionGenerationError` is
raised only when Claude's response can't be bound to the questions schema.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any, Final

from src.ai.claude import (
    ClaudeStructuredOutputError,
    call_claude_structured,
    load_chat_prompt,
)
from src.db.models.job import Job
from src.enums.claude_models import ClaudeModel
from src.enums.fixed_screening_questions import build_fixed_questions
from src.enums.prompts import WHATSAPP_MAX_AI_QUESTIONS as MAX_AI_QUESTIONS
from src.enums.prompts import WHATSAPP_SYSTEM_PROMPT_BASE as SYSTEM_PROMPT_BASE
from src.enums.prompts import WHATSAPP_SYSTEM_PROMPT_STRICT as SYSTEM_PROMPT_STRICT
from src.enums.whatsapp_questions import QuestionCategory
from src.schemas.whatsapp_question import (
    WhatsAppQuestion,
    WhatsAppQuestionDraft,
    WhatsAppQuestionDraftList,
)

logger = logging.getLogger(__name__)

# --- Constants pinned by the architecture doc -------------------------------

# Jinja template that builds the user-message half of the prompt.
PROMPT_TEMPLATE: Final[str] = "generate_whatsapp_questions.j2"

# Tags included in the Claude cache key. The base + strict prompts each
# get their own slot so a strict retry never returns the cached
# bad-output of the base prompt. Bump the ``v2`` segment whenever the
# template or system prompt semantics change.
PROMPT_VERSION_BASE: Final[str] = "generate_whatsapp_questions:base:v7"
PROMPT_VERSION_STRICT: Final[str] = "generate_whatsapp_questions:strict:v7"

# secrets.token_urlsafe(n) returns ceil(4n/3) chars; we slice to QUESTION_ID_LENGTH.
QUESTION_ID_LENGTH: Final[int] = 8

# ``MAX_AI_QUESTIONS`` (the ceiling on questions Claude authors) and the system
# prompts are imported from ``src.enums.prompts``.

# Per-call max_tokens. A 3-question bilingual JSON list with reasoning
# fits comfortably under 2k tokens; bump if Claude starts truncating.
MAX_TOKENS: Final[int] = 2048


# ``SYSTEM_PROMPT_BASE`` / ``SYSTEM_PROMPT_STRICT`` are imported from
# ``src.enums.prompts`` (data-driven by ``QuestionCategory`` so the enum stays
# the single source of truth; the strict variant prepends a corrective reminder
# and routes to a distinct cache slot via ``PROMPT_VERSION_STRICT``).


# Built once: literal system turn + the Jinja human template. The base and
# strict variants share the human template but carry different system prompts,
# matching the two cache slots (PROMPT_VERSION_BASE / _STRICT).
_PROMPT_BASE: Final = load_chat_prompt(
    system=SYSTEM_PROMPT_BASE, human_template_file=PROMPT_TEMPLATE
)
_PROMPT_STRICT: Final = load_chat_prompt(
    system=SYSTEM_PROMPT_STRICT, human_template_file=PROMPT_TEMPLATE
)


class WhatsAppQuestionGenerationError(RuntimeError):
    """Raised when Claude's response can't be coerced into valid questions."""

    def __init__(self, message: str, *, raw: Any) -> None:
        super().__init__(message)
        self.raw = raw


# --- Helpers ----------------------------------------------------------------


def _compensation_line(job: Job) -> str:
    """Format the salary band for the prompt, hiding the field entirely when unset."""
    if job.min_salary is None and job.max_salary is None:
        return "(not specified)"
    low: int | str = job.min_salary if job.min_salary is not None else "?"
    high: int | str = job.max_salary if job.max_salary is not None else "?"
    return f"{job.currency} {low} - {high}"


def _build_template_context(job: Job) -> dict[str, Any]:
    """Pull exactly the fields the prompt needs from a Job ORM object.

    Deliberately excludes ``ai_verify_flags`` — reserved for a future step.
    """
    return {
        "title": job.title,
        "hiring_company": job.hiring_company,
        "country": job.country,
        "city": job.city,
        "employment_type": job.employment_type.value,
        "work_mode": job.work_mode.value,
        "compensation": _compensation_line(job),
        "notice_period": job.notice_period.value if job.notice_period else None,
        "min_experience_years": job.min_experience_years,
        "required_skills": list(job.required_skills),
        "preferred_skills": list(job.preferred_skills),
        "visa_requirement": (job.visa_requirement.value if job.visa_requirement else None),
        "languages_required": list(job.languages_required),
        "job_description": job.job_description,
    }


def _mint_question_id() -> str:
    """Return ``q_`` + 8 url-safe chars. Patched in tests for determinism."""
    return "q_" + secrets.token_urlsafe(QUESTION_ID_LENGTH)[:QUESTION_ID_LENGTH]


# --- Public API -------------------------------------------------------------


def _coerce_ai_drafts(result: WhatsAppQuestionDraftList) -> list[WhatsAppQuestionDraft]:
    """Keep only ``background_validation`` drafts, capped at ``MAX_AI_QUESTIONS``.

    Per-item shape (category enum, non-empty bilingual text, no stray keys) is
    validated by structured output against :class:`WhatsAppQuestionDraftList`.
    The AI is instructed to author ``background_validation`` items only; any
    stray commitment/salary draft is dropped (those are field-sourced). An empty
    result is allowed — the field-sourced questions already cover everything
    else, so there is nothing to raise about here.
    """
    background = [
        d for d in result.questions if d.category is QuestionCategory.BACKGROUND_VALIDATION
    ]
    dropped = len(result.questions) - len(background)
    if dropped:
        logger.warning(
            "whatsapp_question_generator.dropped_non_background",
            extra={"dropped": dropped},
        )
    if len(background) > MAX_AI_QUESTIONS:
        logger.warning(
            "whatsapp_question_generator.over_cap",
            extra={"returned": len(background), "cap": MAX_AI_QUESTIONS},
        )
        background = background[:MAX_AI_QUESTIONS]
    return background


async def _call_with_prompt(
    *,
    variables: dict[str, Any],
    prompt: Any,
    prompt_version: str,
) -> WhatsAppQuestionDraftList:
    try:
        return await call_claude_structured(
            model=ClaudeModel.HAIKU,
            prompt=prompt,
            variables=variables,
            schema=WhatsAppQuestionDraftList,
            max_tokens=MAX_TOKENS,
            prompt_version=prompt_version,
        )
    except ClaudeStructuredOutputError as exc:
        # The model failed to produce schema-valid questions (wrong category,
        # empty Arabic, stray keys, …). Surface as the service's own error so
        # the pipeline marks the step failed. ``raw=None`` — we never carry or
        # log the unbound response content.
        raise WhatsAppQuestionGenerationError(
            "Claude response could not be bound to the questions schema",
            raw=None,
        ) from exc


async def _generate_ai_background_drafts(job: Job) -> list[WhatsAppQuestionDraft]:
    """Ask Claude for up to ``MAX_AI_QUESTIONS`` background_validation drafts.

    Tries the base prompt; if it yields no usable background_validation item,
    retries **once** with the stricter prompt. An empty list is an acceptable
    final result — the field-sourced questions already guarantee a
    background_validation item, so there is nothing to raise about.
    """
    variables = _build_template_context(job)

    result_base = await _call_with_prompt(
        variables=variables,
        prompt=_PROMPT_BASE,
        prompt_version=PROMPT_VERSION_BASE,
    )
    drafts = _coerce_ai_drafts(result_base)
    if drafts:
        return drafts

    logger.warning("whatsapp_question_generator.no_background_validation_retrying")
    result_strict = await _call_with_prompt(
        variables=variables,
        prompt=_PROMPT_STRICT,
        prompt_version=PROMPT_VERSION_STRICT,
    )
    return _coerce_ai_drafts(result_strict)


def _to_question(
    draft: WhatsAppQuestionDraft,
    *,
    order: int,
    is_ai_generated: bool,
    source_field: str | None,
    ai_verifies_response: bool,
) -> WhatsAppQuestion:
    """Mint an id + attach provenance to a content-only draft."""
    return WhatsAppQuestion(
        id=_mint_question_id(),
        order=order,
        category=draft.category,
        subcategory=draft.subcategory,
        question_en=draft.question_en,
        question_ar=draft.question_ar,
        reasoning=draft.reasoning,
        is_ai_generated=is_ai_generated,
        source_field=source_field,
        ai_verifies_response=ai_verifies_response,
    )


async def generate_whatsapp_questions(*, job: Job) -> list[WhatsAppQuestion]:
    """Build the job's WhatsApp screening list: fixed questions + AI background.

    Composition (in order):

    1. The fixed question set from :mod:`src.enums.fixed_screening_questions`,
       identical for every job (placeholders filled from this job). These carry
       ``is_ai_generated=False``, ``source_field=<template key>``, and
       ``ai_verifies_response=False`` — their answers are stored, never scored.
    2. Up to ``MAX_AI_QUESTIONS`` Claude-authored ``background_validation``
       questions verifying hands-on experience with the role's listed required +
       preferred skills (skills only), appended after. They carry
       ``is_ai_generated=True`` and
       ``ai_verifies_response=True`` — these are the *only* questions whose
       answers are AI-scored. If the base response yields none, one stricter
       retry is attempted; an empty AI result is acceptable (the fixed set still
       stands).

    HR may later add custom questions via ``PATCH /jobs/{id}/whatsapp-questions``;
    those carry all three provenance flags False.

    Args:
        job: The Job ORM instance. Only its attributes are read — no session
            or commit is performed. Callable from sync or async test setups.

    Returns:
        Validated :class:`WhatsAppQuestion` objects ordered 1..N (fixed
        questions first, then AI questions). Never empty — the fixed set is
        always present.

    Raises:
        WhatsAppQuestionGenerationError: Claude's response could not be bound to
            the questions schema (the only hard failure left).
    """
    fixed_pairs = build_fixed_questions(job)

    ai_drafts = await _generate_ai_background_drafts(job)

    questions: list[WhatsAppQuestion] = []
    order = 1
    for key, draft in fixed_pairs:
        questions.append(
            _to_question(
                draft,
                order=order,
                is_ai_generated=False,
                source_field=key,
                ai_verifies_response=False,
            )
        )
        order += 1
    for draft in ai_drafts:
        questions.append(
            _to_question(
                draft,
                order=order,
                is_ai_generated=True,
                source_field=None,
                ai_verifies_response=True,
            )
        )
        order += 1
    return questions
