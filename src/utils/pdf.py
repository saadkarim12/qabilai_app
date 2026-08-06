"""PDF text extraction.

Two public coroutines. :func:`extract_text` returns the flattened CV text
(cached). :func:`extract_page1_signals` returns first-page structural signals —
size-ranked name candidates plus a *de-glued* copy of the page text — that
:func:`extract_text` can't provide, used downstream to tell a candidate's name
from a job-title banner and to read a label-glued email (``Emailfoo@bar.com``)
cleanly.

:func:`extract_text`

1. Returns an empty string for empty input (no cache write).
2. SHA-256-hashes the bytes and checks Redis — re-uploads of an identical
   PDF return instantly without running pypdf.
3. Runs pypdf inside ``asyncio.to_thread`` with a hard
   :data:`PDF_EXTRACTION_TIMEOUT_SECONDS` timeout. A timeout raises
   :class:`PdfExtractionTimeoutError`; the caller can map it to a 504.
4. On a pypdf failure of any kind, logs a warning and returns ``""`` —
   matches the architecture doc: "Corrupt PDF returns empty string + logs
   warning (doesn't crash)".
5. Truncates output to :data:`MAX_EXTRACTED_TEXT_CHARS` before caching.

The PDF bytes and the extracted text are never logged (PII / confidential
CV content); only the sha256 prefix and a size figure appear in log
records.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from dataclasses import dataclass
from typing import Any, Final, cast

import redis.asyncio as aioredis
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from src.config import settings

logger = logging.getLogger(__name__)

# --- Constants pinned by the architecture doc -------------------------------

# Hard wall-clock cap on a single extraction call. A maliciously-crafted
# PDF can otherwise spin pypdf indefinitely.
PDF_EXTRACTION_TIMEOUT_SECONDS: Final[int] = 10

# Sanity cap; downstream Claude calls don't want a 200K-char dump anyway.
MAX_EXTRACTED_TEXT_CHARS: Final[int] = 50_000

# 30-day TTL per architecture doc (matches the embedding cache lifetime).
EXTRACTION_CACHE_TTL_SECONDS: Final[int] = 30 * 24 * 60 * 60

# Bump the ``v1`` segment when the extraction pipeline changes (e.g. moving
# off pypdf) so stale text doesn't survive the rollout.
EXTRACTION_CACHE_KEY_PREFIX: Final[str] = "pdf:extract:v1:sha256:"

# How many of the largest-font page-1 chunks to surface as possible names. The
# name is almost always the single largest chunk, but a section heading can
# rival a title sub-banner, so expose a few and let the consumer's name filter
# (``src.ai.contact_extractor``) pick the first that actually looks like a name.
NAME_CANDIDATE_LIMIT: Final[int] = 8


@dataclass(frozen=True, slots=True)
class Page1Signals:
    """Structural signals read from a CV's first page in one pypdf pass.

    pypdf's flattened :func:`extract_text` discards font size *and* glues
    adjacent text runs together (so an ``Email`` label sticks to the address
    that follows it: ``Emailfoo@bar.com``). The text *visitor* keeps each run
    separate and carries its font size, recovering both signals the
    deterministic contact pass needs:

    - ``name_candidates``: page-1 chunks ordered largest-font-first (deduped,
      capped at :data:`NAME_CANDIDATE_LIMIT`). The candidate's name is rendered
      larger than any heading or job-title banner, so this beats a positional
      guess.
    - ``deglued_text``: every page-1 chunk joined by single spaces, in document
      order. Because runs are space-separated here, a label glued to its value
      in the flattened text is split back apart (``Email foo@bar.com``) — so an
      email regex finds the clean address.

    Both are empty when pypdf can't read the page; callers fall back to the
    flattened text / a positional scan / the LLM.
    """

    name_candidates: list[str]
    deglued_text: str


class PdfExtractionTimeoutError(TimeoutError):
    """Raised when extraction exceeds ``PDF_EXTRACTION_TIMEOUT_SECONDS``."""


# --- Lazy factory -----------------------------------------------------------
# Not cached — see ``src/ai/claude.py`` for the rationale. tl;dr: Celery
# tasks each call ``asyncio.run`` which closes the loop; an aioredis
# client bound to a closed loop raises "Event loop is closed" on the
# next task. Constructing per call is cheap (no network until first
# command).


def _redis_client() -> aioredis.Redis:
    client = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, decode_responses=True
    )
    return cast(aioredis.Redis, client)


# --- Helpers ----------------------------------------------------------------


def _cache_key(pdf_bytes: bytes) -> str:
    return EXTRACTION_CACHE_KEY_PREFIX + hashlib.sha256(pdf_bytes).hexdigest()


def _extract_sync(pdf_bytes: bytes) -> str:
    """Run pypdf and return the joined page text, capped + sanitised.

    Any pypdf-side failure (including the generic-``Exception`` safety net
    for binding-level surprises) becomes a warning log and an empty
    string. Matches the architecture doc verbatim.
    """
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
    except PyPdfError as exc:
        logger.warning(
            "pdf.extract_failed_pypdf",
            extra={"error_type": type(exc).__name__, "size_bytes": len(pdf_bytes)},
        )
        return ""
    except Exception as exc:
        # Safety net: pypdf occasionally surfaces stdlib errors (KeyError,
        # struct.error, ...) on truly malformed input. Don't crash the
        # worker over it.
        logger.warning(
            "pdf.extract_failed_generic",
            extra={"error_type": type(exc).__name__, "size_bytes": len(pdf_bytes)},
        )
        return ""

    text = "\n".join(pages)
    if len(text) > MAX_EXTRACTED_TEXT_CHARS:
        text = text[:MAX_EXTRACTED_TEXT_CHARS]
    return text


_EMPTY_PAGE1_SIGNALS: Final = Page1Signals(name_candidates=[], deglued_text="")


def _page1_signals_sync(pdf_bytes: bytes) -> Page1Signals:
    """Read page-1 chunks once and derive both signals (see :class:`Page1Signals`).

    Uses pypdf's text *visitor* so each positioned run stays separate (carrying
    its font size) rather than being flattened and glued. Any pypdf-side
    failure degrades to :data:`_EMPTY_PAGE1_SIGNALS` (mirrors
    :func:`_extract_sync`); the caller treats "no signals" as a reason to fall
    back to the flattened text / the LLM rather than crash.
    """
    chunks: list[tuple[float, str]] = []

    def _visitor(text: str, _cm: Any, _tm: Any, _font: Any, font_size: Any) -> None:
        collapsed = " ".join(text.split())
        if not collapsed:
            return
        try:
            size = float(font_size)
        except (TypeError, ValueError):
            size = 0.0
        chunks.append((size, collapsed))

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if not reader.pages:
            return _EMPTY_PAGE1_SIGNALS
        reader.pages[0].extract_text(visitor_text=_visitor)
    except PyPdfError as exc:
        logger.warning(
            "pdf.page1_signals_failed_pypdf",
            extra={"error_type": type(exc).__name__, "size_bytes": len(pdf_bytes)},
        )
        return _EMPTY_PAGE1_SIGNALS
    except Exception as exc:
        # Same safety net as _extract_sync: malformed input can surface stdlib
        # errors through pypdf. A failed hint must never break the upload.
        logger.warning(
            "pdf.page1_signals_failed_generic",
            extra={"error_type": type(exc).__name__, "size_bytes": len(pdf_bytes)},
        )
        return _EMPTY_PAGE1_SIGNALS

    # De-glued text: chunks in document order, space-joined so a label run no
    # longer abuts the value run that followed it.
    deglued_text = " ".join(chunk for _, chunk in chunks)

    # Name candidates: stable sort by descending size → ties keep top-to-bottom
    # document order; dedup; cap. ``sorted`` (not in-place) so ``deglued_text``
    # above keeps document order.
    name_candidates: list[str] = []
    seen: set[str] = set()
    for _, chunk in sorted(chunks, key=lambda c: -c[0]):
        if chunk in seen:
            continue
        seen.add(chunk)
        name_candidates.append(chunk)
        if len(name_candidates) >= NAME_CANDIDATE_LIMIT:
            break

    return Page1Signals(name_candidates=name_candidates, deglued_text=deglued_text)


# --- Public API -------------------------------------------------------------


async def extract_text(pdf_bytes: bytes) -> str:
    """Extract text from ``pdf_bytes`` with cache + timeout + char cap.

    Returns an empty string for empty input or for any pypdf failure.

    Raises:
        PdfExtractionTimeoutError: if extraction takes longer than
            ``PDF_EXTRACTION_TIMEOUT_SECONDS`` seconds.
    """
    if not pdf_bytes:
        return ""

    key = _cache_key(pdf_bytes)
    redis = _redis_client()

    cached = await redis.get(key)
    if cached is not None:
        logger.info(
            "pdf.extract_cache_hit",
            extra={"sha256_prefix": key.removeprefix(EXTRACTION_CACHE_KEY_PREFIX)[:12]},
        )
        return cast(str, cached)

    try:
        text = await asyncio.wait_for(
            asyncio.to_thread(_extract_sync, pdf_bytes),
            timeout=PDF_EXTRACTION_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        logger.warning(
            "pdf.extract_timeout",
            extra={
                "size_bytes": len(pdf_bytes),
                "timeout_seconds": PDF_EXTRACTION_TIMEOUT_SECONDS,
            },
        )
        raise PdfExtractionTimeoutError(
            f"PDF extraction exceeded {PDF_EXTRACTION_TIMEOUT_SECONDS}s"
        ) from exc

    # Cache empty results too — a corrupt PDF stays corrupt, no point
    # re-running pypdf on every re-upload.
    await redis.set(key, text, ex=EXTRACTION_CACHE_TTL_SECONDS)
    logger.info(
        "pdf.extract_done",
        extra={
            "size_bytes": len(pdf_bytes),
            "chars_extracted": len(text),
            "sha256_prefix": key.removeprefix(EXTRACTION_CACHE_KEY_PREFIX)[:12],
        },
    )
    return text


async def extract_page1_signals(pdf_bytes: bytes) -> Page1Signals:
    """First-page structural signals (name candidates + de-glued text), one pass.

    On essentially every CV the candidate's name is the largest text on the
    first page (bigger than headings / a job-title banner) and the contact
    block sits there too. Plain :func:`extract_text` flattens the page —
    discarding font size *and* gluing labels to their values — so the
    deterministic pass in
    :func:`src.ai.contact_extractor.extract_contact_details` can neither
    reliably pick the name nor read a label-glued email from it. This surfaces
    both signals from pypdf's text visitor (see :class:`Page1Signals`).

    Runs pypdf under the same wall-clock guard as :func:`extract_text`, over
    the first page only. Returns empty signals for empty input, a timeout, or
    any pypdf failure — these hints are best-effort, so a failure degrades to
    "no signals" (the caller falls back to the flattened text / a positional
    scan / the LLM) rather than aborting the upload the way a missing *text*
    extraction would.

    Intentionally **not** Redis-cached: it parses only page 1, so a re-upload
    re-runs it cheaply and a second cache round-trip isn't worth it. The PDF
    bytes and the extracted chunks are never logged (PII).
    """
    if not pdf_bytes:
        return _EMPTY_PAGE1_SIGNALS

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_page1_signals_sync, pdf_bytes),
            timeout=PDF_EXTRACTION_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "pdf.page1_signals_timeout",
            extra={
                "size_bytes": len(pdf_bytes),
                "timeout_seconds": PDF_EXTRACTION_TIMEOUT_SECONDS,
            },
        )
        return _EMPTY_PAGE1_SIGNALS
