"""Lightweight contact-detail extractor for the HR bulk-upload endpoint.

The bulk uploader needs the minimum identity (full_name + email +
phone) inline — before returning 202 — so HR sees per-file "parse
failed" rejections in the same response as their upload, rather than
having to come back later to check why a candidate row didn't show up.

The full :mod:`src.ai.cv_parser` pipeline (work history, skills,
authenticity, embeddings) still runs asynchronously in the Step 3.9
Celery chain. This module only covers the *gate* into the system.

Cost / latency: a **deterministic pass runs first** — email via regex and
phone via :mod:`phonenumbers` (the libraries the rest of the system already
uses), plus the name taken from the **largest-font text on page 1**. CVs
render the name bigger than any heading or job-title banner, so the size
signal beats a positional guess and needs no assumption that the name appears
in the email. The email is searched in the **de-glued page-1 text** rather
than the flattened CV text, because pypdf's flatten concatenates a label run
with the value run that follows it (``Emailfoo@bar.com``), which the regex
would otherwise read as the address. Both signals come from one
:func:`src.utils.pdf.extract_page1_signals` pass (the flatten discards them).
When that pass is *unambiguous* (exactly one email, one phone, and a confident
name) it returns immediately, skipping Claude entirely — the common case for
clean CVs, turning a ~1-2 s synchronous Claude call into a sub-millisecond one.
Any ambiguity or missing field (or a PDF whose page pypdf couldn't measure)
falls back to one Claude Haiku call (the original behavior), so accuracy is
never sacrificed for the saving.
The 7-day Redis cache in
:func:`src.ai.claude.call_claude_structured` keyed on the rendered prompt
(which embeds the CV text) means re-uploading the same PDF never re-calls
Claude; CV text is sha256-cached upstream by
:func:`src.utils.pdf.extract_text`, so re-uploading the same bytes also
short-circuits the pypdf extraction.

CV text and Claude responses are *never* logged — they're PII /
confidential. Log records carry only an emit flag and field-presence
counts.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Final

import phonenumbers
from pydantic import BaseModel, ConfigDict

from src.ai.claude import (
    ClaudeStructuredOutputError,
    call_claude_structured,
    load_chat_prompt,
)
from src.config import settings
from src.enums.claude_models import ClaudeModel
from src.enums.prompts import CONTACT_EXTRACT_SYSTEM_PROMPT as SYSTEM_PROMPT
from src.schemas.parsed_cv import EMAIL_MAX_LENGTH, FULL_NAME_MAX_LENGTH, PHONE_MAX_LENGTH
from src.utils.phone import InvalidPhoneNumberError, normalize_phone

logger = logging.getLogger(__name__)

# --- Constants pinned by the architecture doc -------------------------------

# Jinja template for the user-message half of the prompt.
PROMPT_TEMPLATE: Final[str] = "extract_contact_details.j2"

# Cache key tag. Separate from cv_parser's tags so a contact-extract cache
# entry is never served as a full-parse response (or vice-versa).
PROMPT_VERSION: Final[str] = "extract_contact_details:v1"

# Contact details fit in tens of tokens; cap low so a runaway response
# can't burn the budget.
MAX_TOKENS: Final[int] = 512

# Defensive cap mirroring cv_parser.MAX_INPUT_CHARS — pdf.extract_text
# already truncates to 50k, but contact details cluster at the top of a
# CV so we feed only the first slice for both cost and signal-to-noise.
MAX_INPUT_CHARS: Final[int] = 8_000

# Basic RFC-5322-ish sanity check; matches cv_parser's pattern so the
# two extractors apply the same drop-to-None rule on bogus emails.
_EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Un-anchored variant used to *find* email addresses inside free CV text
# (``_EMAIL_PATTERN`` is anchored for full-string validation). Matches the
# pattern used by the logging redactor so the two stay consistent. Every hit
# is still re-validated through ``_sanitize_email``.
_EMAIL_SEARCH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)

# Name heuristic (positional fallback): used only when no font-size candidates
# are available (text-only callers / tests). A name sits at the very top of the
# CV, so scan the first handful of non-empty lines. Bias conservative — when the
# line doesn't clearly look like a name we return None and let the LLM decide.
# The primary path is size-ranked candidates from extract_page1_signals; see
# _resolve_name.
_NAME_SCAN_LINES: Final[int] = 10
_NAME_MIN_TOKENS: Final[int] = 2
_NAME_MAX_TOKENS: Final[int] = 4
# Lower-cased tokens that mark a header/label line, never a person's name.
_NAME_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "curriculum",
        "vitae",
        "resume",
        "résumé",
        "cv",
        "profile",
        "summary",
        "contact",
        "details",
        "information",
        "personal",
        "objective",
        "about",
        "name",
    }
)

# ``SYSTEM_PROMPT`` is imported from ``src.enums.prompts`` (high volume, narrow
# rubric, short output; Haiku is the right tier per ClaudeModel docstring).

# Built once at import: literal system turn + the Jinja human template. The
# model returns the answer as structured (tool-call) output bound to
# ``ContactDetailsDraft`` — no free-text JSON parsing.
_CONTACT_PROMPT: Final = load_chat_prompt(system=SYSTEM_PROMPT, human_template_file=PROMPT_TEMPLATE)


class ContactDetailsDraft(BaseModel):
    """Content-only shape Claude returns from the contact-extract prompt.

    ``extra='forbid'`` keeps Claude from sneaking through unrequested
    keys (it has a habit of inventing ``confidence``-style fields).
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = None
    email: str | None = None
    phone: str | None = None


class ContactDetails(BaseModel):
    """Sanitized contact details.

    ``email`` is dropped to ``None`` if it doesn't match the basic
    pattern; ``phone`` is dropped if it doesn't normalize to E.164.
    The bulk-upload service treats *any* of name/email/phone being
    ``None`` here as "parse failed: no contact" and rejects the file.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = None
    email: str | None = None
    phone: str | None = None


def _sanitize_full_name(raw: str | None) -> str | None:
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    if not stripped or len(stripped) > FULL_NAME_MAX_LENGTH:
        return None
    return stripped


def _sanitize_email(raw: str | None) -> str | None:
    if not isinstance(raw, str):
        return None
    stripped = raw.strip().lower()
    if not stripped or len(stripped) > EMAIL_MAX_LENGTH:
        return None
    if not _EMAIL_PATTERN.match(stripped):
        return None
    return stripped


def _sanitize_phone(raw: str | None) -> str | None:
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    if not stripped or len(stripped) > PHONE_MAX_LENGTH:
        return None
    try:
        return normalize_phone(stripped, default_region=settings.default_phone_region)
    except InvalidPhoneNumberError:
        return None


def _empty() -> ContactDetails:
    return ContactDetails()


# --- Deterministic pass -----------------------------------------------------


def _find_emails(text: str) -> list[str]:
    """Return distinct, sanitized emails found in ``text`` (order preserved)."""
    seen: dict[str, None] = {}
    for match in _EMAIL_SEARCH_PATTERN.finditer(text):
        clean = _sanitize_email(match.group(0))
        if clean is not None:
            seen.setdefault(clean, None)
    return list(seen)


def _find_phones(text: str) -> list[str]:
    """Return distinct E.164 phones found in ``text`` (order preserved).

    The matcher region is ``settings.default_phone_region``. When unset
    (``None``) only numbers carrying an explicit country code (``+…``) are
    matched — national-format numbers are left for the LLM. When set (e.g.
    ``"PK"``) bare national numbers in that region are matched too, mirroring
    :func:`normalize_phone`. ``Leniency.POSSIBLE`` mirrors ``normalize_phone``'s
    ``is_possible`` check so the two agree on what counts as a phone number.
    """
    seen: dict[str, None] = {}
    for match in phonenumbers.PhoneNumberMatcher(
        text, settings.default_phone_region, leniency=phonenumbers.Leniency.POSSIBLE
    ):
        clean = _sanitize_phone(match.raw_string)
        if clean is not None:
            seen.setdefault(clean, None)
    return list(seen)


def _looks_like_name(line: str) -> bool:
    """True if ``line`` plausibly *is* a person's name (not a header/label)."""
    if not line or len(line) > FULL_NAME_MAX_LENGTH:
        return False
    if "@" in line or any(ch.isdigit() for ch in line):
        return False  # email, phone, date, or address line
    if any(sep in line for sep in ("/", "\\", ":", "|", "•")):
        return False  # urls, "Email:" labels, separator-laden header lines
    tokens = line.split()
    if not (_NAME_MIN_TOKENS <= len(tokens) <= _NAME_MAX_TOKENS):
        return False
    if any(tok.lower().strip(".,") in _NAME_STOPWORDS for tok in tokens):
        return False
    for tok in tokens:
        # Allow straight apostrophes and hyphens (O'Brien, Al-Hassan); accented
        # letters are already alpha. Curly-apostrophe names fall back to the LLM.
        core = re.sub(r"[-']", "", tok)
        if not core.isalpha() or not tok[0].isupper():
            return False
    return True


def _heuristic_name(text: str) -> str | None:
    """Return the first top-of-document line that looks like a name, else None."""
    scanned = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        scanned += 1
        if scanned > _NAME_SCAN_LINES:
            break
        if _looks_like_name(line):
            return line
    return None


def _resolve_name(text: str, name_candidates: Sequence[str] | None) -> str | None:
    """Pick the candidate's name, preferring the font-size signal.

    ``name_candidates`` is page-1 text ordered largest-font first (from
    :func:`src.utils.pdf.extract_page1_signals`). On a CV the name is
    rendered larger than any heading or job-title banner, so the first
    candidate that passes :func:`_looks_like_name` *is* the name — no
    assumption that the name appears in the email, and language-agnostic.
    When size info is present but nothing looks like a name, return ``None``
    so the caller defers to Claude rather than guessing from position.

    With no size info — an empty/absent candidate list (text-only callers /
    tests, or a PDF pypdf couldn't measure) — fall back to the
    top-of-document line scan.
    """
    if name_candidates:
        for candidate in name_candidates:
            name = _sanitize_full_name(candidate)
            if name is not None and _looks_like_name(name):
                return name
        return None
    return _sanitize_full_name(_heuristic_name(text))


def _deterministic_contact(
    text: str,
    name_candidates: Sequence[str] | None,
    contact_block_text: str | None,
) -> ContactDetails | None:
    """Extract all three fields without the LLM, or None to fall back.

    Returns a populated :class:`ContactDetails` only when the pass is
    *unambiguous* — exactly one email, one phone, and a confident name (the
    largest-font name candidate, or the top-of-document line when no size
    signal is available). Any ambiguity (multiple emails/phones) or a missing
    field returns ``None`` so the caller defers to the Claude fallback.

    Email is searched in ``contact_block_text`` when supplied — the page-1
    *de-glued* text from :func:`src.utils.pdf.extract_page1_signals`, where a
    label no longer abuts its value (``Email foo@bar.com``), so the regex
    doesn't swallow the ``Email`` label into the address. Phone keeps using
    the flattened ``text``: :mod:`phonenumbers` already ignores a glued
    ``Phone`` label, and re-sourcing a field that isn't broken would only risk
    splitting a number across de-glued chunks.
    """
    emails = _find_emails(contact_block_text or text)
    phones = _find_phones(text)
    if len(emails) != 1 or len(phones) != 1:
        return None
    name = _resolve_name(text, name_candidates)
    if name is None:
        return None
    return ContactDetails(full_name=name, email=emails[0], phone=phones[0])


async def extract_contact_details(
    cv_text: str,
    name_candidates: Sequence[str] | None = None,
    contact_block_text: str | None = None,
) -> ContactDetails:
    """Extract ``(full_name, email, phone)`` from ``cv_text``.

    Empty / whitespace-only input short-circuits to an empty
    :class:`ContactDetails` without any work. Otherwise a deterministic
    pass (regex email + :mod:`phonenumbers` + size-ranked name) runs first;
    when it unambiguously finds all three fields the result is returned
    without calling Claude. Any ambiguity or missing field falls back to
    the Claude Haiku extractor. Validation failures degrade to ``None``
    per field rather than raising — the caller treats a missing field as
    a "parse_failed_no_contact" rejection.

    Args:
        cv_text: Raw text extracted from a CV (see
            :func:`src.utils.pdf.extract_text`).
        name_candidates: Page-1 text ordered largest-font first (from
            :func:`src.utils.pdf.extract_page1_signals`). When supplied, the
            deterministic name is the largest chunk that looks like a name —
            this avoids mistaking a job-title banner for the name on designed
            CVs. Omit (``None``) and the pass falls back to a top-of-document
            line scan; callers with the PDF bytes should pass it.
        contact_block_text: Page-1 *de-glued* text (also from
            :func:`src.utils.pdf.extract_page1_signals`). When supplied, the
            deterministic email is searched here instead of ``cv_text`` so a
            label glued to the address in the flattened text
            (``Emailfoo@bar.com``) doesn't poison the match. Omit (``None``)
            and email search falls back to ``cv_text``.

    Returns:
        A :class:`ContactDetails` with each field either populated
        and valid or ``None``.
    """
    if not cv_text or not cv_text.strip():
        logger.info("contact_extractor.empty_input")
        return _empty()

    text = cv_text[:MAX_INPUT_CHARS]
    block_text = contact_block_text[:MAX_INPUT_CHARS] if contact_block_text else None

    deterministic = _deterministic_contact(text, name_candidates, block_text)
    if deterministic is not None:
        logger.info(
            "contact_extractor.deterministic",
            extra={"has_full_name": True, "has_email": True, "has_phone": True},
        )
        return deterministic

    return await _llm_extract_contact_details(text)


async def _llm_extract_contact_details(text: str) -> ContactDetails:
    """Claude Haiku fallback when the deterministic pass is inconclusive.

    ``text`` is already truncated to :data:`MAX_INPUT_CHARS` by the caller.
    Claude returns the answer as tool-bound structured output validated
    against :class:`ContactDetailsDraft`; if it fails to bind (rare, given the
    tool schema constrains the shape) we degrade to an empty result rather
    than raise — the caller treats that as "no contact, parse failed".
    """
    try:
        draft = await call_claude_structured(
            model=ClaudeModel.HAIKU,
            prompt=_CONTACT_PROMPT,
            variables={"cv_text": text},
            schema=ContactDetailsDraft,
            max_tokens=MAX_TOKENS,
            prompt_version=PROMPT_VERSION,
        )
    except ClaudeStructuredOutputError:
        logger.warning("contact_extractor.unbound_response")
        return _empty()

    sanitized = ContactDetails(
        full_name=_sanitize_full_name(draft.full_name),
        email=_sanitize_email(draft.email),
        phone=_sanitize_phone(draft.phone),
    )
    logger.info(
        "contact_extractor.extracted",
        extra={
            "has_full_name": sanitized.full_name is not None,
            "has_email": sanitized.email is not None,
            "has_phone": sanitized.phone is not None,
        },
    )
    return sanitized
