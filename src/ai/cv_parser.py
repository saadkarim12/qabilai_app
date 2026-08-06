"""CV parsing service.

Single public coroutine :func:`parse_cv`. It

1. Short-circuits empty / whitespace-only input to an empty
   :class:`~src.schemas.parsed_cv.ParsedCV` with
   :attr:`~src.enums.parsed_cv.ParseStatus.OK`. No Claude call, no
   exception — matches the doc's "Empty CV produces empty fields, not
   crash" acceptance criterion.
2. Renders the parse-CV prompt with the supplied CV text and language
   hint, then calls Claude (Sonnet — routine extraction; the doc lists
   Haiku but :class:`~src.enums.claude_models.ClaudeModel` exposes only
   Sonnet/Opus, and per project mandate Sonnet covers routine extraction)
   via tool-use structured output bound to :class:`ParsedCVDraft`.
3. If the response can't be bound to the schema, retries **once** with a
   stricter system prompt; the retry has its own ``prompt_version`` so it
   doesn't share a cache slot with the base prompt.
4. If the strict retry also fails to bind, performs field-by-field
   *salvage* on the model's raw tool arguments — keeps every value that
   validates in isolation, drops only the bad pieces — and returns the
   result with ``parse_status=NEEDS_REVIEW``.
5. Sanitizes the extracted ``email`` (basic regex) and ``phone``
   (E.164 normalization via :func:`src.utils.phone.normalize_phone`).
   Invalid values are dropped to ``None`` rather than raising; the
   parse still succeeds.

The CV text, the rendered prompt, and Claude's response are *never*
logged — they're PII / confidential. Log records carry only the
language code, field counts, ``parse_status``, and a ``retried`` /
``salvaged`` flag.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final

from pydantic import ValidationError

from src.ai.claude import (
    ClaudeStructuredOutputError,
    call_claude_structured,
    load_chat_prompt,
)
from src.config import settings
from src.enums.claude_models import ClaudeModel
from src.enums.language import LanguageCode
from src.enums.parsed_cv import ParseStatus
from src.enums.prompts import PARSE_CV_SYSTEM_PROMPT_BASE as SYSTEM_PROMPT_BASE
from src.enums.prompts import PARSE_CV_SYSTEM_PROMPT_STRICT as SYSTEM_PROMPT_STRICT
from src.schemas.parsed_cv import (
    EDUCATION_MAX_COUNT,
    EMAIL_MAX_LENGTH,
    FULL_NAME_MAX_LENGTH,
    LANGUAGE_NAME_MAX_LENGTH,
    LANGUAGES_MAX_COUNT,
    SKILL_MAX_LENGTH,
    SKILLS_MAX_COUNT,
    TOTAL_EXPERIENCE_YEARS_MAX,
    TOTAL_EXPERIENCE_YEARS_MIN,
    WORK_HISTORY_MAX_COUNT,
    EducationEntry,
    ParsedCV,
    ParsedCVDraft,
    WorkEntry,
)
from src.utils.phone import InvalidPhoneNumberError, normalize_phone

logger = logging.getLogger(__name__)

# --- Constants pinned by the architecture doc -------------------------------

# Jinja template that builds the user-message half of the prompt.
PROMPT_TEMPLATE: Final[str] = "parse_cv.j2"

# Tag included in the Claude cache key. The base + strict prompts each get
# their own slot so a strict retry never returns the cached base-prompt result.
# Bump the ``v1`` segment whenever the schema or system prompt semantics change.
PROMPT_VERSION_BASE: Final[str] = "parse_cv:base:v1"
PROMPT_VERSION_STRICT: Final[str] = "parse_cv:strict:v1"

# Per-call max_tokens. A long CV with full work history fits comfortably under
# this; bump if Claude starts truncating long-careered profiles.
MAX_TOKENS: Final[int] = 4096

# Mirror the PDF extractor's cap — anything longer was already truncated
# upstream, but enforce here defensively in case parse_cv is called with raw
# text from another source.
MAX_INPUT_CHARS: Final[int] = 50_000

# Basic RFC-5322-ish sanity check. Not exhaustive — the goal is to drop
# obviously-bogus values ("see CV" / "n/a" / "candidate@" with no domain),
# not validate deliverability. Doc says "basic regex validation".
_EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ``SYSTEM_PROMPT_BASE`` / ``SYSTEM_PROMPT_STRICT`` are imported from
# ``src.enums.prompts``. The field caps they cite (skills/work/etc.) come from
# ``src.schemas.parsed_cv`` so the prompt and the schema stay in sync.


# Built once: literal system turn + the Jinja human template. Base and strict
# share the human template but carry different system prompts, matching the two
# cache slots (PROMPT_VERSION_BASE / _STRICT).
_PROMPT_BASE: Final = load_chat_prompt(
    system=SYSTEM_PROMPT_BASE, human_template_file=PROMPT_TEMPLATE
)
_PROMPT_STRICT: Final = load_chat_prompt(
    system=SYSTEM_PROMPT_STRICT, human_template_file=PROMPT_TEMPLATE
)


# --- Sanitization helpers ---------------------------------------------------


def _sanitize_email(raw: str | None) -> str | None:
    """Return ``raw`` if it looks like an email address, else ``None``."""
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped or len(stripped) > EMAIL_MAX_LENGTH:
        return None
    if not _EMAIL_PATTERN.match(stripped):
        return None
    return stripped


def _sanitize_phone(raw: str | None) -> str | None:
    """Return E.164-formatted phone or ``None``.

    Defers to :func:`src.utils.phone.normalize_phone`. Any
    :class:`InvalidPhoneNumberError` becomes ``None`` — the doc explicitly
    says "must normalize to E.164 or null", so dropping is correct.
    """
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        return normalize_phone(stripped, default_region=settings.default_phone_region)
    except InvalidPhoneNumberError:
        return None


# --- Salvage ----------------------------------------------------------------


def _salvage_string(
    value: Any,
    *,
    max_length: int,
) -> str | None:
    """Best-effort string coercion: must be a non-empty ``str`` within length."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or len(stripped) > max_length:
        return None
    return stripped


def _salvage_string_list(
    value: Any,
    *,
    max_count: int,
    max_item_length: int,
) -> list[str]:
    """Drop non-string items and items violating length caps."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if not stripped or len(stripped) > max_item_length:
            continue
        out.append(stripped)
        if len(out) >= max_count:
            break
    return out


def _salvage_work_entry(value: Any) -> WorkEntry | None:
    """Per-entry validation; returns ``None`` if the entry is unsalvageable."""
    if not isinstance(value, dict):
        return None
    try:
        return WorkEntry.model_validate(value)
    except ValidationError:
        return None


def _salvage_education_entry(value: Any) -> EducationEntry | None:
    if not isinstance(value, dict):
        return None
    try:
        return EducationEntry.model_validate(value)
    except ValidationError:
        return None


def _salvage_entry_list(
    value: Any,
    *,
    max_count: int,
    validator: Any,
) -> list[Any]:
    """Validate each entry individually; drop only the bad ones."""
    if not isinstance(value, list):
        return []
    out: list[Any] = []
    for item in value:
        entry = validator(item)
        if entry is not None:
            out.append(entry)
            if len(out) >= max_count:
                break
    return out


def _salvage_total_experience(value: Any) -> float | None:
    """Coerce to float and clamp to [MIN, MAX]; ``None`` if uncoercible."""
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    as_float = float(value)
    if as_float < TOTAL_EXPERIENCE_YEARS_MIN or as_float > TOTAL_EXPERIENCE_YEARS_MAX:
        return None
    return as_float


def _coerce_optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _salvage(raw: dict[str, Any]) -> ParsedCVDraft:
    """Build a ``ParsedCVDraft`` from whatever fields validate in isolation.

    The strict retry has already failed at this point — we still want to
    preserve as much as we can so HR has a starting point on review.

    Phone and email are pre-sanitized here (rather than relying on
    :func:`_finalize`) so the salvaged draft always satisfies the
    ``ParsedCVDraft`` length caps even when Claude returned overlong /
    free-form values.
    """
    return ParsedCVDraft(
        full_name=_salvage_string(raw.get("full_name"), max_length=FULL_NAME_MAX_LENGTH),
        email=_sanitize_email(_coerce_optional_str(raw.get("email"))),
        phone=_sanitize_phone(_coerce_optional_str(raw.get("phone"))),
        skills=_salvage_string_list(
            raw.get("skills"),
            max_count=SKILLS_MAX_COUNT,
            max_item_length=SKILL_MAX_LENGTH,
        ),
        work_history=_salvage_entry_list(
            raw.get("work_history"),
            max_count=WORK_HISTORY_MAX_COUNT,
            validator=_salvage_work_entry,
        ),
        education=_salvage_entry_list(
            raw.get("education"),
            max_count=EDUCATION_MAX_COUNT,
            validator=_salvage_education_entry,
        ),
        total_experience_years=_salvage_total_experience(raw.get("total_experience_years")),
        languages=_salvage_string_list(
            raw.get("languages"),
            max_count=LANGUAGES_MAX_COUNT,
            max_item_length=LANGUAGE_NAME_MAX_LENGTH,
        ),
    )


# --- Claude orchestration ---------------------------------------------------


async def _call_with_prompt(
    *,
    variables: dict[str, Any],
    prompt: Any,
    prompt_version: str,
) -> ParsedCVDraft:
    """Call Claude for a schema-bound :class:`ParsedCVDraft`.

    Raises :class:`ClaudeStructuredOutputError` if the response can't be bound;
    the model's raw tool arguments ride on ``.raw_args`` for the salvage path.
    """
    return await call_claude_structured(
        model=ClaudeModel.SONNET,
        prompt=prompt,
        variables=variables,
        schema=ParsedCVDraft,
        max_tokens=MAX_TOKENS,
        prompt_version=prompt_version,
    )


def _finalize(draft: ParsedCVDraft, *, status: ParseStatus) -> ParsedCV:
    """Apply email/phone sanitization and wrap the draft in a ParsedCV."""
    sanitized_email = _sanitize_email(draft.email)
    sanitized_phone = _sanitize_phone(draft.phone)
    return ParsedCV(
        full_name=draft.full_name,
        email=sanitized_email,
        phone=sanitized_phone,
        skills=draft.skills,
        work_history=draft.work_history,
        education=draft.education,
        total_experience_years=draft.total_experience_years,
        languages=draft.languages,
        parse_status=status,
    )


# --- Public API -------------------------------------------------------------


def _log_parsed(result: ParsedCV, *, language: LanguageCode, retried: bool) -> None:
    """Emit the PII-safe success log line (counts + flags only)."""
    logger.info(
        "cv_parser.parsed",
        extra={
            "language": language.value,
            "parse_status": result.parse_status.value,
            "retried": retried,
            "salvaged": False,
            "skill_count": len(result.skills),
            "work_count": len(result.work_history),
            "education_count": len(result.education),
        },
    )


async def parse_cv(text: str, language: LanguageCode) -> ParsedCV:
    """Parse ``text`` into a :class:`ParsedCV`.

    Empty / whitespace-only input short-circuits to an empty ``ParsedCV``
    without calling Claude. Otherwise the service tries the base prompt,
    retries once with the strict prompt when the response can't be bound to
    the schema, and falls back to field-by-field salvage of the model's raw
    tool arguments on the second failure (returning the result with
    :attr:`ParseStatus.NEEDS_REVIEW`).

    Email and phone are sanitized after parse: invalid values become
    ``None`` rather than raising. The candidate still lands in the system.

    Args:
        text: Raw text extracted from a CV (see ``src.utils.pdf.extract_text``).
        language: Detected language of the CV (``EN``/``AR``/``MIXED``).
            Passed through to the prompt as a hint.

    Returns:
        A validated :class:`ParsedCV`. Never raises on Claude content
        failures — those land as ``parse_status=NEEDS_REVIEW``.
    """
    if not text or not text.strip():
        logger.info("cv_parser.empty_input", extra={"language": language.value})
        return ParsedCV(parse_status=ParseStatus.OK)

    # Defensive cap — the PDF extractor already enforces this, but parse_cv
    # may be called with text from other sources later.
    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS]

    variables = {"cv_text": text, "language": language.value}
    base_raw_args: dict[str, Any] = {}
    strict_raw_args: dict[str, Any] = {}

    # Base attempt.
    try:
        draft = await _call_with_prompt(
            variables=variables, prompt=_PROMPT_BASE, prompt_version=PROMPT_VERSION_BASE
        )
    except ClaudeStructuredOutputError as base_err:
        base_raw_args = base_err.raw_args
        logger.warning("cv_parser.validation_failed_retrying", extra={"language": language.value})
    else:
        result = _finalize(draft, status=ParseStatus.OK)
        _log_parsed(result, language=language, retried=False)
        return result

    # Strict retry.
    try:
        draft_strict = await _call_with_prompt(
            variables=variables, prompt=_PROMPT_STRICT, prompt_version=PROMPT_VERSION_STRICT
        )
    except ClaudeStructuredOutputError as strict_err:
        strict_raw_args = strict_err.raw_args
    else:
        result = _finalize(draft_strict, status=ParseStatus.OK)
        _log_parsed(result, language=language, retried=True)
        return result

    # Strict retry also failed — salvage what we can from the raw tool args,
    # preferring the strict attempt's output, falling back to the base's.
    salvage_dict = strict_raw_args if strict_raw_args else base_raw_args
    salvaged_draft = _salvage(salvage_dict)
    result = _finalize(salvaged_draft, status=ParseStatus.NEEDS_REVIEW)
    logger.warning(
        "cv_parser.salvaged",
        extra={
            "language": language.value,
            "parse_status": result.parse_status.value,
            "retried": True,
            "salvaged": True,
            "skill_count": len(result.skills),
            "work_count": len(result.work_history),
            "education_count": len(result.education),
        },
    )
    return result
