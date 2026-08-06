"""Public CV upload intake (Step 3.8).

Single public coroutine :func:`intake_application` orchestrates the
public-apply flow:

1. **Honeypot**: a non-empty honeypot field is a bot — log + return a
   randomly-generated reference number, no DB writes. Same response
   shape as the happy path so an attacker can't tell.
2. **Slug → Job**: look up the active ``PublicUploadToken`` for the
   slug; 5-minute Redis cache to keep the per-upload lookup cheap.
   Unknown / inactive slug → :class:`SlugNotFoundError` (HTTP 410).
3. **Consent**: must be ``True`` or raise
   :class:`ConsentNotGivenError` (400).
4. **Email / phone validation**: existing :mod:`src.utils.phone` and
   a basic email regex. Failures map to 400.
5. **PDF validation + upload**: :mod:`src.integrations.azure_blob`
   handles magic-byte + size checks and computes sha256. We catch its
   library errors and re-raise as domain errors so the HTTP handler
   can map them.
6. **Candidate find-or-create** on the composite identity
   ``(LOWER(email), phone_e164)``.
7. **CV dedup**: look up an existing :class:`CvDocument` with the same
   sha256 for this candidate; if found, reuse it. Else demote the
   candidate's current CV (set ``is_current=false``) and insert a new
   row pointing at the freshly uploaded blob.
8. **Application find-or-create** on ``(candidate_id, job_id)`` — the
   unique constraint guarantees idempotence for the doc's "same
   candidate applying twice" criterion.
9. **Enqueue** the stub CV pipeline dispatcher (Step 3.9 fills in the
   real chain).
10. Return the :class:`Application` row; the route layer commits and
    formats the response.

The whole flow runs inside the request's single :class:`AsyncSession`
transaction. The route layer (not this service) is responsible for the
final ``commit()`` so test-mode rollback can undo the writes.
"""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import Final, cast

import redis.asyncio as aioredis
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models.application import Application
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.public_upload_token import PublicUploadToken
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.exceptions import (
    ConsentNotGivenError,
    InvalidEmailError,
    PdfInvalidError,
    PdfTooLargeError,
    SlugNotFoundError,
)
from src.integrations.azure_blob import (
    FileTooLargeError,
    PdfMagicBytesError,
    UploadedBlob,
    upload_pdf,
)
from src.schemas.public_apply import ConsentContext, PublicApplyResponse
from src.utils.phone import InvalidPhoneNumberError, normalize_phone

logger = logging.getLogger(__name__)

# --- Slug cache (Step 3.8 doc: "Slug → job_id lookup cached in Redis for 5 min") -------

SLUG_CACHE_KEY_PREFIX: Final[str] = "public_slug:v1:"
SLUG_CACHE_TTL_SECONDS: Final[int] = 5 * 60

# Sentinel cached for *unknown* slugs so a flood of bogus requests can't
# bypass the cache and hammer the DB. Same TTL — short enough that a slug
# becoming active is picked up quickly.
SLUG_CACHE_MISS_SENTINEL: Final[str] = "__none__"

# --- Email validation -------------------------------------------------------
# Same minimal-format check the CV parser uses for sanitization. Stricter
# semantic validation (MX lookup, deliverability) belongs in a verification
# step, not at upload time.
_EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_MAX_LENGTH: Final[int] = 254  # RFC 5321

# --- Misc -------------------------------------------------------------------

FULL_NAME_MAX_LENGTH: Final[int] = 255
FULL_NAME_MIN_LENGTH: Final[int] = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redis_client() -> aioredis.Redis:
    """Open (and cache) the Redis connection used for slug lookups."""
    client = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, decode_responses=True
    )
    return cast(aioredis.Redis, client)


def _slug_cache_key(slug: str) -> str:
    return SLUG_CACHE_KEY_PREFIX + slug


async def _resolve_slug_to_job_id(session: AsyncSession, slug: str) -> uuid.UUID:
    """Return the active Job UUID for ``slug`` or raise SlugNotFoundError.

    Caches the resolution (or its miss) in Redis for 5 minutes. Redis
    failures degrade to a direct DB read with a warning log — they
    never surface as a 5xx to the candidate.
    """
    cache_key = _slug_cache_key(slug)
    redis = _redis_client()

    try:
        cached = await redis.get(cache_key)
    except Exception:
        logger.warning("public_apply.slug_cache_get_failed", extra={"slug_prefix": slug[:6]})
        cached = None

    if cached == SLUG_CACHE_MISS_SENTINEL:
        raise SlugNotFoundError("Public upload slug is unknown or inactive.")
    if cached:
        try:
            return uuid.UUID(cached)
        except ValueError:
            # Defensive: a malformed cache value just falls through to a DB read.
            logger.warning("public_apply.slug_cache_corrupt", extra={"slug_prefix": slug[:6]})

    stmt = sa.select(PublicUploadToken.job_id).where(
        PublicUploadToken.slug == slug,
        PublicUploadToken.is_active.is_(True),
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        try:
            await redis.setex(cache_key, SLUG_CACHE_TTL_SECONDS, SLUG_CACHE_MISS_SENTINEL)
        except Exception:
            logger.warning("public_apply.slug_cache_set_failed", extra={"slug_prefix": slug[:6]})
        raise SlugNotFoundError("Public upload slug is unknown or inactive.")

    try:
        await redis.setex(cache_key, SLUG_CACHE_TTL_SECONDS, str(row))
    except Exception:
        logger.warning("public_apply.slug_cache_set_failed", extra={"slug_prefix": slug[:6]})
    return row


def _validate_email(email: str) -> str:
    """Normalize + basic-validate the submitted email. Raises InvalidEmailError."""
    cleaned = email.strip().lower()
    if not cleaned or len(cleaned) > EMAIL_MAX_LENGTH or not _EMAIL_PATTERN.match(cleaned):
        raise InvalidEmailError("Email is not in a valid format.")
    return cleaned


def _validate_full_name(full_name: str) -> str:
    cleaned = full_name.strip()
    if not (FULL_NAME_MIN_LENGTH <= len(cleaned) <= FULL_NAME_MAX_LENGTH):
        raise InvalidEmailError(
            "Full name is required.",
            error_code="invalid_full_name",
            http_status=400,
        )
    return cleaned


async def _find_or_create_candidate(
    session: AsyncSession,
    *,
    email: str,
    phone_e164: str,
    full_name: str,
) -> Candidate:
    """Composite-key lookup (LOWER(email), phone_e164). Insert if missing."""
    stmt = sa.select(Candidate).where(
        sa.func.lower(Candidate.email) == email.lower(),
        Candidate.phone_e164 == phone_e164,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    candidate = Candidate(email=email, phone_e164=phone_e164, full_name=full_name)
    session.add(candidate)
    await session.flush()
    return candidate


async def _find_existing_cv_document(
    session: AsyncSession, *, candidate_id: uuid.UUID, sha256: str
) -> CvDocument | None:
    stmt = sa.select(CvDocument).where(
        CvDocument.candidate_id == candidate_id,
        CvDocument.blob_sha256 == sha256,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _demote_current_cv_document(session: AsyncSession, *, candidate_id: uuid.UUID) -> None:
    """Flip any ``is_current=True`` row for this candidate to False.

    The partial UNIQUE on ``cv_documents(candidate_id) WHERE is_current``
    requires we do this *before* inserting a new current row.
    """
    await session.execute(
        sa.update(CvDocument)
        .where(CvDocument.candidate_id == candidate_id, CvDocument.is_current.is_(True))
        .values(is_current=False)
    )


async def _upload_cv_document(
    session: AsyncSession, *, candidate_id: uuid.UUID, pdf_bytes: bytes
) -> CvDocument:
    """Upload the PDF, demote any prior current CV, insert a new row."""
    try:
        blob: UploadedBlob = await upload_pdf(pdf_bytes, candidate_id=candidate_id)
    except PdfMagicBytesError as exc:
        raise PdfInvalidError(str(exc)) from exc
    except FileTooLargeError as exc:
        raise PdfTooLargeError(str(exc)) from exc

    await _demote_current_cv_document(session, candidate_id=candidate_id)

    cv_doc = CvDocument(
        candidate_id=candidate_id,
        blob_url=blob.url,
        blob_sha256=blob.sha256,
        is_current=True,
    )
    session.add(cv_doc)
    await session.flush()
    return cv_doc


async def _find_or_create_application(
    session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    job_id: uuid.UUID,
    cv_document_id: uuid.UUID,
    consent_context: ConsentContext,
) -> tuple[Application, bool]:
    """Composite-key (candidate, job) lookup; insert if missing.

    Returns ``(application, was_created)`` so the caller can decide
    whether to enqueue the CV pipeline — duplicates skip the enqueue
    (the pipeline already ran or is in-flight from the first
    submission).

    Archived stints (move-to-pool) are excluded from the lookup so a
    candidate re-applying after leaving the pipeline starts a fresh
    application rather than resurrecting a dead one — matching the partial
    unique index, which only enforces uniqueness on non-archived rows.
    """
    stmt = sa.select(Application).where(
        Application.candidate_id == candidate_id,
        Application.job_id == job_id,
        Application.status != ApplicationStatus.ARCHIVED,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing, False

    application = Application(
        candidate_id=candidate_id,
        job_id=job_id,
        cv_document_id=cv_document_id,
        stage=ApplicationStage.VECTOR_SCREEN,
        status=ApplicationStatus.ACTIVE,
        consent_context=consent_context.model_dump(mode="json"),
        consented_at=consent_context.accepted_at,
    )
    session.add(application)
    await session.flush()
    return application, True


def _enqueue_cv_pipeline(application_id: uuid.UUID) -> None:
    """Dispatch the Step 3.9 CV pipeline via the stub task.

    Lives in its own function so tests can monkey-patch the enqueue
    without touching Celery's broker config. Imports inside the function
    to avoid Celery initialization at module-import time (matches the
    pattern jobs_routes uses).
    """
    from src.workers.tasks.cv_pipeline import dispatch_cv_processing_task

    dispatch_cv_processing_task.delay(str(application_id))


def _honeypot_response() -> PublicApplyResponse:
    """Build a bogus 202 response for honeypot-tripped requests.

    Same shape as the real response so a bot can't tell its submission
    was rejected. The reference number is a random hex string that
    never lands in the DB.
    """
    return PublicApplyResponse(reference_number=secrets.token_hex(16))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def intake_application(
    session: AsyncSession,
    *,
    slug: str,
    pdf_bytes: bytes,
    email: str,
    phone: str,
    full_name: str,
    consent: bool,
    ip: str,
    user_agent: str,
    honeypot: str | None,
) -> PublicApplyResponse:
    """Process a public-apply upload end-to-end.

    See the module docstring for the step-by-step flow. The caller
    (route handler) owns the transaction — this function ``flush``-es
    its writes so they're queryable within the request, but does not
    commit.
    """
    # --- Honeypot short-circuit (no DB writes, no log of submitter data) ---
    if honeypot:
        logger.warning(
            "public_apply.honeypot_triggered",
            extra={"slug_prefix": slug[:6], "honeypot_length": len(honeypot)},
        )
        return _honeypot_response()

    # --- Consent first — refuse early if missing -----------------------------
    if consent is not True:
        raise ConsentNotGivenError("Consent must be accepted to submit an application.")

    # --- Slug → job (cached) -------------------------------------------------
    job_id = await _resolve_slug_to_job_id(session, slug)

    # --- Field validation ----------------------------------------------------
    normalized_email = _validate_email(email)
    cleaned_full_name = _validate_full_name(full_name)
    try:
        phone_e164 = normalize_phone(phone)
    except InvalidPhoneNumberError as exc:
        # Re-raise as a domain error so the global exception handler
        # turns it into a 400, not a 500.
        raise InvalidEmailError(
            str(exc),
            error_code="invalid_phone",
            http_status=400,
        ) from exc

    # --- Consent context (validated up-front so a bad ip / UA fails fast) ---
    consent_context = ConsentContext(
        ip=ip,
        user_agent=user_agent,
        accepted_at=datetime.now(UTC),
    )

    # --- Candidate -----------------------------------------------------------
    candidate = await _find_or_create_candidate(
        session,
        email=normalized_email,
        phone_e164=phone_e164,
        full_name=cleaned_full_name,
    )

    # --- CV: dedup by sha256, else upload + demote prior current -----------
    # Hash the bytes once so dedup uses the same digest the upload would
    # produce. Magic-byte and size validation are deferred to upload_pdf
    # (single source of truth) — if dedup misses, the upload runs them.
    import hashlib

    sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    existing_cv = await _find_existing_cv_document(
        session, candidate_id=candidate.id, sha256=sha256
    )
    if existing_cv is not None:
        cv_document = existing_cv
        logger.info(
            "public_apply.cv_dedup_hit",
            extra={"candidate_id": str(candidate.id), "sha256_prefix": sha256[:12]},
        )
    else:
        cv_document = await _upload_cv_document(
            session, candidate_id=candidate.id, pdf_bytes=pdf_bytes
        )

    # --- Application (idempotent on candidate+job) ---------------------------
    application, was_created = await _find_or_create_application(
        session,
        candidate_id=candidate.id,
        job_id=job_id,
        cv_document_id=cv_document.id,
        consent_context=consent_context,
    )

    # --- Enqueue CV pipeline only for genuinely new applications -------------
    if was_created:
        _enqueue_cv_pipeline(application.id)

    logger.info(
        "public_apply.intake_ok",
        extra={
            "application_id": str(application.id),
            "candidate_created": existing_cv is None,
            "application_created": was_created,
            "cv_dedup_hit": existing_cv is not None,
        },
    )

    return PublicApplyResponse(reference_number=application.id.hex)
