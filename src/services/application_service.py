"""Application listing / detail / audit-log service.

Mirrors :mod:`src.services.job_service` patterns: pure async functions
over an ``AsyncSession``, domain exceptions on failure, ``flush()`` not
``commit()`` so the route layer owns the transaction boundary.

The list endpoint is wrapped in a 15-second Redis cache keyed on
``(job_id, stage, status, order, page, page_size)``. The architecture
doc calls for cache invalidation on stage / status change; we get that
for free by including ``order_by`` outputs that change with those
transitions plus a short TTL — explicit DELETE on every transition
would be both expensive and racy against parallel writes.

Detail and audit-log are uncached: their reads are PK / index seeks,
the volumes are small, and cache invalidation on score writes (Step
3.9 / 4.3) would be a maintenance burden we don't yet need.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Final, cast

import redis.asyncio as aioredis
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models.application import Application
from src.db.models.application_score import ApplicationScore
from src.db.models.audit_log import AuditLog
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.interview_booking import InterviewBooking
from src.db.models.user import User
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.audit import AuditAction, AuditEntityType
from src.enums.authenticity import AuthenticityBand
from src.enums.score_types import ScoreModel, ScoreType
from src.enums.stages import (
    ALLOWED_STAGE_TRANSITIONS,
    ALLOWED_STATUS_TRANSITIONS,
    CONTACT_EDITABLE_STAGES,
)
from src.exceptions import (
    ApplicationNotFoundError,
    CandidateContactLockedError,
    CandidateIdentityConflictError,
    IllegalApplicationStatusTransitionError,
    IllegalStageTransitionError,
    InterviewFeedbackStageError,
    InvalidEmailError,
    JobNotFoundError,
)
from src.integrations.azure_blob import CV_BLOB_PATH_TEMPLATE, get_signed_url
from src.schemas.application import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ApplicationDetailResponse,
    ApplicationListItemResponse,
    ApplicationListOrder,
    ApplicationListResponse,
    ApplicationScoreResponse,
    AuditLogEntryResponse,
    AuditLogResponse,
    CandidateNested,
    CvDocumentNested,
    InterviewBookingNested,
)
from src.services.scoring.similarity_breakdown import derive_rejection_reason
from src.utils.percentage import format_percent, format_percent_optional
from src.utils.phone import InvalidPhoneNumberError, normalize_phone

logger = logging.getLogger(__name__)

# --- Cache constants ---------------------------------------------------------

APP_LIST_CACHE_KEY_PREFIX: Final[str] = "application:list:"
APP_LIST_CACHE_TTL_SECONDS: Final[int] = 15

# --- Pagination re-exports for the route layer ------------------------------

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "audit_rescore_requested",
    "get_application_audit_log",
    "get_application_detail",
    "list_applications_cached",
    "system_reject_application",
    "update_application_candidate",
    "update_application_stage",
    "update_application_status",
    "update_interview_feedback",
]

# Minimal email-format check, mirroring the public-apply intake validator.
# Stricter semantic validation (MX / deliverability) belongs in a dedicated
# verification step, not at edit time.
_EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_MAX_LENGTH: Final[int] = 254  # RFC 5321 hard cap

# --- Ordering ---------------------------------------------------------------
#
# Mapping from the public ``order=`` value to the SQLAlchemy expression
# used in ``ORDER BY``. NULL-handling matters for the score-based orders
# because most pre-pipeline rows have NULL similarity / hard_filter:
# putting NULLs last in DESC sorts keeps "best-scored first" intuitive.

_ORDER_EXPRS: Final[dict[ApplicationListOrder, tuple[Any, ...]]] = {
    ApplicationListOrder.CREATED_AT_DESC: (
        Application.created_at.desc(),
        Application.id.asc(),
    ),
    ApplicationListOrder.CREATED_AT_ASC: (
        Application.created_at.asc(),
        Application.id.asc(),
    ),
    ApplicationListOrder.SIMILARITY_DESC: (
        Application.similarity_score.desc().nullslast(),
        Application.id.asc(),
    ),
    ApplicationListOrder.SIMILARITY_ASC: (
        Application.similarity_score.asc().nullslast(),
        Application.id.asc(),
    ),
    ApplicationListOrder.HARD_FILTER_DESC: (
        Application.hard_filter_score.desc().nullslast(),
        Application.id.asc(),
    ),
    ApplicationListOrder.HARD_FILTER_ASC: (
        Application.hard_filter_score.asc().nullslast(),
        Application.id.asc(),
    ),
    ApplicationListOrder.STAGE_UPDATED_DESC: (
        Application.stage_updated_at.desc(),
        Application.id.asc(),
    ),
}


def _redis_client() -> aioredis.Redis:
    """Lazy Redis client. Tests monkey-patch this symbol to inject fakes."""
    client = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, decode_responses=False
    )
    return cast(aioredis.Redis, client)


def _list_cache_key(
    job_id: uuid.UUID,
    *,
    stage: ApplicationStage | None,
    status: ApplicationStatus | None,
    order: ApplicationListOrder,
    page: int,
    page_size: int,
) -> str:
    """Stable cache key for one (job, filter set, page) tuple."""
    stage_part = stage.value if stage is not None else "_"
    status_part = status.value if status is not None else "_"
    return (
        f"{APP_LIST_CACHE_KEY_PREFIX}{job_id}:"
        f"s={stage_part}:st={status_part}:o={order.value}:p={page}:ps={page_size}"
    )


# --- Verify job existence ----------------------------------------------------


async def _ensure_job_exists(session: AsyncSession, job_id: uuid.UUID) -> None:
    """404 if the job id is unknown — saves an empty-list reply that
    would otherwise mask a bad URL.

    Imports ``Job`` locally to avoid a circular import between this
    module and ``job_service`` if either grows new imports later.
    """
    from src.db.models.job import Job

    exists = await session.execute(sa.select(Job.id).where(Job.id == job_id))
    if exists.scalar_one_or_none() is None:
        raise JobNotFoundError(f"Job {job_id} not found")


# --- List --------------------------------------------------------------------


async def _list_applications(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    stage: ApplicationStage | None,
    status: ApplicationStatus | None,
    order: ApplicationListOrder,
    page: int,
    page_size: int,
) -> ApplicationListResponse:
    """Uncached list query — selectin-loads the candidate for the name + email columns."""
    offset = (page - 1) * page_size

    where_clauses: list[sa.ColumnElement[bool]] = [Application.job_id == job_id]
    if stage is not None:
        where_clauses.append(Application.stage == stage)
    if status is not None:
        where_clauses.append(Application.status == status)
    else:
        # Archived stints (moved back to the talent pool) aren't part of the
        # job's active pipeline — hide them unless explicitly asked for via
        # ``status=archived``. The candidate's history endpoint surfaces them.
        where_clauses.append(Application.status != ApplicationStatus.ARCHIVED)

    order_clause = _ORDER_EXPRS[order]

    # Application has no SQLAlchemy ``relationship`` to Candidate today,
    # so we fan out two queries (applications + their candidates by id)
    # and join in Python. Cheap: page_size <= 100 means at most one IN
    # with 100 ids, and Postgres serves that off the candidates PK.
    items_stmt = (
        sa.select(Application)
        .where(*where_clauses)
        .order_by(*order_clause)
        .offset(offset)
        .limit(page_size)
    )

    rows = (await session.execute(items_stmt)).scalars().all()

    if rows:
        candidate_ids = {row.candidate_id for row in rows}
        cands = (
            (await session.execute(sa.select(Candidate).where(Candidate.id.in_(candidate_ids))))
            .scalars()
            .all()
        )
        cand_by_id = {c.id: c for c in cands}
    else:
        cand_by_id = {}

    items: list[ApplicationListItemResponse] = []
    for app in rows:
        cand = cand_by_id.get(app.candidate_id)
        items.append(
            ApplicationListItemResponse(
                id=app.id,
                candidate_id=app.candidate_id,
                candidate_full_name=cand.full_name if cand is not None else "",
                candidate_email=cand.email if cand is not None else None,
                job_id=app.job_id,
                stage=app.stage,
                status=app.status,
                similarity_score=format_percent_optional(app.similarity_score),
                hard_filter_score=format_percent_optional(app.hard_filter_score),
                interview_score=format_percent_optional(app.interview_score),
                stage_updated_at=app.stage_updated_at,
                created_at=app.created_at,
            )
        )

    count_stmt = sa.select(sa.func.count()).select_from(Application).where(*where_clauses)
    total_raw = (await session.execute(count_stmt)).scalar_one()
    total = int(total_raw or 0)

    return ApplicationListResponse(items=items, total=total, page=page, page_size=page_size)


async def list_applications_cached(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    stage: ApplicationStage | None,
    status: ApplicationStatus | None,
    order: ApplicationListOrder,
    page: int,
    page_size: int,
) -> ApplicationListResponse:
    """Public list entrypoint — 15s Redis cache layered on the SQL query.

    Cache miss → run :func:`_list_applications`, serialise to JSON, store.
    Cache hit → parse the JSON and return.

    Redis outages are logged at WARNING and never bubble to the client;
    they just degrade to direct DB reads.
    """
    page_size = min(page_size, MAX_PAGE_SIZE)
    await _ensure_job_exists(session, job_id)

    key = _list_cache_key(
        job_id, stage=stage, status=status, order=order, page=page, page_size=page_size
    )
    redis = _redis_client()

    try:
        cached = await redis.get(key)
    except Exception:
        logger.warning("application_service.list_cache.get_failed", extra={"job_id": str(job_id)})
        cached = None

    if cached is not None:
        decoded = cached.decode("utf-8") if isinstance(cached, bytes) else cached
        return ApplicationListResponse.model_validate(json.loads(decoded))

    response = await _list_applications(
        session,
        job_id=job_id,
        stage=stage,
        status=status,
        order=order,
        page=page,
        page_size=page_size,
    )
    try:
        await redis.set(key, response.model_dump_json(), ex=APP_LIST_CACHE_TTL_SECONDS)
    except Exception:
        logger.warning("application_service.list_cache.set_failed", extra={"job_id": str(job_id)})

    return response


# --- Detail ------------------------------------------------------------------


async def _signed_cv_url(cv_doc: CvDocument) -> str:
    """Return a short-lived SAS URL the browser can actually open.

    ``cv_documents.blob_url`` is the bare blob URL with no token. The
    storage account blocks public access (correct posture), so a browser
    hitting that raw URL gets ``PublicAccessNotPermitted``. Rebuild the
    deterministic blob path (``cvs/{candidate_id}/{sha256}.pdf``) and mint a
    read-only SAS via :func:`get_signed_url`.

    On any signing failure — Blob storage not configured in this env (tests,
    local), Redis down, malformed conn string — fall back to the raw
    ``blob_url`` rather than 500 the whole detail response. The worst case is
    no worse than today: the link just doesn't open.
    """
    blob_path = CV_BLOB_PATH_TEMPLATE.format(
        candidate_id=cv_doc.candidate_id, sha256=cv_doc.blob_sha256
    )
    try:
        return await get_signed_url(blob_path)
    except Exception:
        logger.warning("application.cv_url_sign_failed", extra={"cv_document_id": str(cv_doc.id)})
        return cv_doc.blob_url


async def get_application_detail(
    session: AsyncSession, application_id: uuid.UUID
) -> ApplicationDetailResponse:
    """Return the full nested application detail or 404.

    Issues three reads: the application row, the candidate, the current
    CV. Plus one query for the score history. All keyed on PKs / a
    one-shot indexed scan, so total query cost is bounded regardless of
    how many score rows exist.
    """
    app = await session.get(Application, application_id)
    if app is None:
        raise ApplicationNotFoundError(f"Application {application_id} not found")

    candidate = await session.get(Candidate, app.candidate_id)
    cv_doc = await session.get(CvDocument, app.cv_document_id)
    if candidate is None or cv_doc is None:
        # FK constraints (RESTRICT on cv_documents, CASCADE on candidates)
        # make these missing only if the row was deleted out-of-band.
        # Treat as 404 for the application — anything else is a 500 in
        # disguise that we'd rather expose as "not found" than crash.
        raise ApplicationNotFoundError(f"Application {application_id} has missing related rows")

    scores_stmt = (
        sa.select(ApplicationScore)
        .where(ApplicationScore.application_id == application_id)
        .order_by(ApplicationScore.computed_at.desc(), ApplicationScore.id.asc())
    )
    score_rows = (await session.execute(scores_stmt)).scalars().all()

    # Pull the most recent similarity score's breakdown — the score rows
    # are already sorted newest-first, so the first SIMILARITY row wins.
    # Older rows (hard_filter, prior rescores) are skipped intentionally.
    latest_similarity_breakdown: dict[str, Any] | None = None
    for s in score_rows:
        if s.score_type == ScoreType.SIMILARITY:
            latest_similarity_breakdown = s.breakdown
            break

    rejection_reason = derive_rejection_reason(
        similarity_breakdown=latest_similarity_breakdown,
        status_is_rejected=app.status == ApplicationStatus.REJECTED,
    )

    score_responses = [ApplicationScoreResponse.model_validate(s) for s in score_rows]

    # The interview booking (Step 6.4), if HR has reached that stage. One row
    # per application keyed on application_id; ``None`` before the invite.
    booking = (
        await session.execute(
            sa.select(InterviewBooking).where(InterviewBooking.application_id == application_id)
        )
    ).scalar_one_or_none()
    interview = InterviewBookingNested.model_validate(booking) if booking is not None else None

    # Authenticity is stored denormalised (not an ``application_scores`` row),
    # but HR wants every score's reasoning in one place, so we synthesize it as
    # the ``authenticity`` entry. Prefer the application's job-aware verdict
    # (six signals, incl. JD Keyword Mirroring); fall back to the candidate's
    # job-agnostic baseline until the application-level run lands. ``id`` is
    # None precisely because no row backs it.
    auth_score: float | None
    auth_band: AuthenticityBand | None
    auth_breakdown: dict[str, Any] | None
    auth_computed_at: datetime | None
    if app.authenticity_computed_at is not None and app.authenticity_score is not None:
        auth_score = app.authenticity_score
        auth_band = app.authenticity_band
        auth_breakdown = app.authenticity_breakdown
        auth_computed_at = app.authenticity_computed_at
    else:
        auth_score = candidate.authenticity_score
        auth_band = candidate.authenticity_band
        auth_breakdown = candidate.authenticity_breakdown
        auth_computed_at = candidate.authenticity_computed_at

    if auth_computed_at is not None and auth_score is not None:
        score_responses.append(
            ApplicationScoreResponse(
                id=None,
                score_type=ScoreType.AUTHENTICITY,
                value=format_percent(auth_score),
                breakdown=auth_breakdown or {},
                prompt_version="-",
                model_used=ScoreModel.CLAUDE_SONNET.value,
                computed_at=auth_computed_at,
            )
        )
        # Keep the whole list newest-first so the synthesized entry sorts in
        # chronologically with the persisted rows.
        score_responses.sort(key=lambda s: s.computed_at, reverse=True)

    return ApplicationDetailResponse(
        id=app.id,
        job_id=app.job_id,
        candidate=CandidateNested(
            id=candidate.id,
            email=candidate.email,
            phone_e164=candidate.phone_e164,
            full_name=candidate.full_name,
            authenticity_score=format_percent_optional(auth_score),
            authenticity_band=(auth_band.value if auth_band is not None else None),
            authenticity_computed_at=auth_computed_at,
            parsed_profile=candidate.parsed_profile,
        ),
        cv_document=CvDocumentNested(
            id=cv_doc.id,
            blob_url=await _signed_cv_url(cv_doc),
            blob_sha256=cv_doc.blob_sha256,
            language=cv_doc.language.value if cv_doc.language is not None else None,
            uploaded_at=cv_doc.uploaded_at,
        ),
        stage=app.stage,
        status=app.status,
        similarity_score=format_percent_optional(app.similarity_score),
        hard_filter_score=format_percent_optional(app.hard_filter_score),
        interview_score=format_percent_optional(app.interview_score),
        interview_comment=app.interview_comment,
        interview_scored_at=app.interview_scored_at,
        rejection_reason=rejection_reason,
        pipeline_status=app.pipeline_status,
        consent_context=app.consent_context,
        consented_at=app.consented_at,
        scores=score_responses,
        interview=interview,
        stage_updated_at=app.stage_updated_at,
        created_at=app.created_at,
        updated_at=app.updated_at,
    )


# --- Audit log ---------------------------------------------------------------


async def get_application_audit_log(
    session: AsyncSession, application_id: uuid.UUID
) -> AuditLogResponse:
    """Return chronological (newest-first) audit log for one application.

    Filters on ``(entity_type='application', entity_id=application_id)``
    — the composite index makes this a single index seek + range scan.

    Returns an empty list if no entries exist yet (Step 4.1 ships the
    endpoint; Step 4.2 wires the first writes).
    """
    # Verify the application exists so a typo'd id yields 404, not "[]".
    exists_stmt = sa.select(Application.id).where(Application.id == application_id)
    if (await session.execute(exists_stmt)).scalar_one_or_none() is None:
        raise ApplicationNotFoundError(f"Application {application_id} not found")

    stmt = (
        sa.select(AuditLog)
        .where(
            AuditLog.entity_type == AuditEntityType.APPLICATION.value,
            AuditLog.entity_id == application_id,
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()

    items = [AuditLogEntryResponse.model_validate(r) for r in rows]
    return AuditLogResponse(items=items, total=len(items))


# --- Step 4.2: stage / status transitions -----------------------------------
#
# Both transition helpers:
#   1. SELECT ... FOR UPDATE — Postgres row lock serialises concurrent
#      PATCHes against the same application (two HR users clicking at
#      the same instant). Without the lock we'd race on the audit-log
#      before-state snapshot.
#   2. Validate against the matrix in ``src.enums.stages``.
#   3. Mutate + write an ``audit_logs`` row inside the same transaction
#      so a rollback discards both atomically.
#   4. Best-effort invalidate the list cache for the affected job — TTL
#      is 15s anyway, so a missed invalidate is bounded staleness.
#
# Detail/audit-log endpoints are uncached so no invalidation needed
# there.


async def _invalidate_list_cache_for_job(job_id: uuid.UUID) -> None:
    """SCAN + DELETE every list-cache key for the given job.

    The key shape is ``application:list:{job_id}:s=...:st=...:o=...:p=...:ps=...``
    so a prefix scan with ``{prefix}{job_id}:*`` finds every cached page
    for every filter set.

    Redis outages are logged at WARNING and swallowed — the 15-second
    TTL is the secondary defence so a missed DELETE only adds bounded
    staleness, not unbounded.
    """
    redis = _redis_client()
    pattern = f"{APP_LIST_CACHE_KEY_PREFIX}{job_id}:*"
    try:
        keys: list[bytes | str] = []
        async for key in redis.scan_iter(match=pattern, count=100):
            keys.append(key)
        if keys:
            await redis.delete(*keys)
    except Exception:
        logger.warning(
            "application_service.list_cache.invalidate_failed",
            extra={"job_id": str(job_id)},
        )


async def _load_application_for_update(
    session: AsyncSession, application_id: uuid.UUID
) -> Application:
    """``SELECT ... FOR UPDATE`` the application or raise 404.

    The row lock prevents two concurrent PATCHes from each reading the
    same before-state and writing inconsistent audit-log rows.
    """
    stmt = sa.select(Application).where(Application.id == application_id).with_for_update()
    app = (await session.execute(stmt)).scalar_one_or_none()
    if app is None:
        raise ApplicationNotFoundError(f"Application {application_id} not found")
    return app


async def update_application_stage(
    session: AsyncSession,
    *,
    application_id: uuid.UUID,
    new_stage: ApplicationStage,
    actor: User,
    reason: str | None,
    ip_address: str | None,
) -> tuple[Application, ApplicationStage]:
    """Move an application to ``new_stage`` if allowed.

    Returns ``(application, entered_stage)`` — ``entered_stage`` is the stage
    just transitioned into — so the route can dispatch the right follow-up
    task (hard-filter scoring, WhatsApp invite) after the transaction commits.

    Rules:
    * Stage moves require ``status == ACTIVE``. A rejected / accepted
      application is frozen at its current stage (per doc).
    * The (old → new) pair must appear in
      :data:`ALLOWED_STAGE_TRANSITIONS`.
    """
    app = await _load_application_for_update(session, application_id)
    old_stage = app.stage
    old_status = app.status

    if old_status is not ApplicationStatus.ACTIVE:
        raise IllegalStageTransitionError(
            f"Cannot change stage while status is {old_status.value!r}",
            details={"current_status": old_status.value},
        )

    if new_stage is old_stage:
        # No-op transitions waste audit log space and are almost certainly
        # an HR UI bug — reject so the bug is visible.
        raise IllegalStageTransitionError(
            f"Application is already in stage {old_stage.value!r}",
            details={"from": old_stage.value, "to": new_stage.value},
        )

    allowed = ALLOWED_STAGE_TRANSITIONS.get(old_stage, frozenset())
    if new_stage not in allowed:
        raise IllegalStageTransitionError(
            f"Cannot move application from {old_stage.value!r} to {new_stage.value!r}",
            details={"from": old_stage.value, "to": new_stage.value},
        )

    app.stage = new_stage
    app.stage_updated_at = datetime.now(UTC)

    after: dict[str, Any] = {"stage": new_stage.value}
    if reason:
        after["reason"] = reason

    session.add(
        AuditLog(
            user_id=actor.id,
            entity_type=AuditEntityType.APPLICATION.value,
            entity_id=app.id,
            action=AuditAction.STAGE_CHANGED.value,
            before_state={"stage": old_stage.value},
            after_state=after,
            ip_address=ip_address,
        )
    )
    await session.flush()

    await _invalidate_list_cache_for_job(app.job_id)
    logger.info(
        "application.stage_changed",
        extra={
            "application_id": str(app.id),
            "old": old_stage.value,
            "new": new_stage.value,
            "user_id": str(actor.id),
        },
    )

    return app, new_stage


async def update_application_status(
    session: AsyncSession,
    *,
    application_id: uuid.UUID,
    new_status: ApplicationStatus,
    actor: User,
    reason: str | None,
    ip_address: str | None,
) -> Application:
    """Move an application to ``new_status`` if allowed.

    Rules from :data:`ALLOWED_STATUS_TRANSITIONS`:

    * ``active → rejected``      (manual)
    * ``active → accepted``      (manual)
    * ``rejected → active``      (HR override; stage unchanged)
    * ``accepted → *``           (terminal, blocked)

    Stage is intentionally NOT modified — the doc requires the rejection
    point to be visible in the row, and the override path puts the
    candidate back into the same stage they were ejected from.

    The audit-log row uses :class:`AuditAction.MANUALLY_ACCEPTED` /
    :class:`AuditAction.MANUALLY_REJECTED` when those are the targets so
    HR's UI can highlight the rationale, and the generic
    :class:`AuditAction.STATUS_CHANGED` for the override path.
    """
    app = await _load_application_for_update(session, application_id)
    old_status = app.status

    if new_status is old_status:
        raise IllegalApplicationStatusTransitionError(
            f"Application is already in status {old_status.value!r}",
            details={"from": old_status.value, "to": new_status.value},
        )

    allowed = ALLOWED_STATUS_TRANSITIONS.get(old_status, frozenset())
    if new_status not in allowed:
        raise IllegalApplicationStatusTransitionError(
            f"Cannot move application from {old_status.value!r} to {new_status.value!r}",
            details={"from": old_status.value, "to": new_status.value},
        )

    app.status = new_status

    if new_status is ApplicationStatus.ACCEPTED:
        action = AuditAction.MANUALLY_ACCEPTED
    elif new_status is ApplicationStatus.REJECTED:
        action = AuditAction.MANUALLY_REJECTED
    else:
        action = AuditAction.STATUS_CHANGED

    after: dict[str, Any] = {"status": new_status.value}
    if reason:
        after["reason"] = reason

    session.add(
        AuditLog(
            user_id=actor.id,
            entity_type=AuditEntityType.APPLICATION.value,
            entity_id=app.id,
            action=action.value,
            before_state={"status": old_status.value},
            after_state=after,
            ip_address=ip_address,
        )
    )
    await session.flush()

    await _invalidate_list_cache_for_job(app.job_id)
    logger.info(
        "application.status_changed",
        extra={
            "application_id": str(app.id),
            "old": old_status.value,
            "new": new_status.value,
            "user_id": str(actor.id),
            "action": action.value,
        },
    )

    return app


async def system_reject_application(
    session: AsyncSession,
    *,
    application: Application,
    reason: str,
    action: AuditAction,
) -> bool:
    """Reject an application as a **system** action (no HR actor).

    Used by the WhatsApp screening flow when the candidate declines. Sets
    ``status=REJECTED`` (stage preserved, per doc), writes an audit row with
    ``user_id=NULL``, and invalidates the job's list cache. Returns whether a
    change was made — a no-op (already non-active) returns ``False`` so the
    caller can avoid sending a redundant closing message.

    Takes the already-loaded ``application`` (the conversation router holds it)
    rather than re-querying. The caller owns the commit.
    """
    old_status = application.status
    if old_status is not ApplicationStatus.ACTIVE:
        # Already rejected/accepted — don't override a terminal decision.
        return False

    application.status = ApplicationStatus.REJECTED
    session.add(
        AuditLog(
            user_id=None,
            entity_type=AuditEntityType.APPLICATION.value,
            entity_id=application.id,
            action=action.value,
            before_state={"status": old_status.value},
            after_state={"status": ApplicationStatus.REJECTED.value, "reason": reason},
            ip_address=None,
        )
    )
    await session.flush()

    await _invalidate_list_cache_for_job(application.job_id)
    logger.info(
        "application.system_rejected",
        extra={
            "application_id": str(application.id),
            "action": action.value,
        },
    )
    return True


# --- Step 4.4: rescore audit ------------------------------------------------


async def audit_rescore_requested(
    session: AsyncSession,
    *,
    application_id: uuid.UUID,
    rescore_type: str,
    actor: User,
    ip_address: str | None,
) -> Application:
    """Verify the application exists and write a ``rescore_requested`` audit row.

    The route layer then commits and dispatches the Celery task with
    ``force=True``. Returning the loaded :class:`Application` lets the
    caller pass its ``id`` through without re-querying.
    """
    app = await session.get(Application, application_id)
    if app is None:
        raise ApplicationNotFoundError(f"Application {application_id} not found")

    session.add(
        AuditLog(
            user_id=actor.id,
            entity_type=AuditEntityType.APPLICATION.value,
            entity_id=app.id,
            action=AuditAction.RESCORE_REQUESTED.value,
            before_state=None,
            after_state={"type": rescore_type},
            ip_address=ip_address,
        )
    )
    await session.flush()

    logger.info(
        "application.rescore_requested",
        extra={
            "application_id": str(app.id),
            "type": rescore_type,
            "user_id": str(actor.id),
        },
    )
    return app


# --- Candidate contact edit -------------------------------------------------


def _normalize_email(raw: str | None) -> str | None:
    """Trim + lower-case + basic-validate an email, or pass ``None`` through.

    Mirrors the candidate model's ``@validates('email')`` lower-casing so a
    read round-trips identically. Raises :class:`InvalidEmailError` (400) on a
    malformed address; an empty/whitespace string is treated as "cleared"
    (``None``) so the at-least-one-contact rule applies uniformly.
    """
    if raw is None:
        return None
    cleaned = raw.strip().lower()
    if not cleaned:
        return None
    if len(cleaned) > EMAIL_MAX_LENGTH or not _EMAIL_PATTERN.match(cleaned):
        raise InvalidEmailError("Email is not in a valid format.")
    return cleaned


def _normalize_phone(raw: str | None) -> str | None:
    """Normalise a phone to E.164, or pass a blank/``None`` through as cleared.

    Raises :class:`InvalidEmailError` with the ``invalid_phone`` code (400) on
    an unparseable number — reusing the generic 400 carrier the intake path
    uses so the global handler maps it cleanly.
    """
    if raw is None or not raw.strip():
        return None
    try:
        return normalize_phone(raw, default_region=settings.default_phone_region)
    except InvalidPhoneNumberError as exc:
        raise InvalidEmailError(str(exc), error_code="invalid_phone", http_status=400) from exc


async def _assert_identity_available(
    session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    email: str | None,
    phone_e164: str | None,
) -> None:
    """Refuse if the new (email, phone) pair already belongs to another candidate.

    Mirrors the three partial-unique indexes from migration 0013 (both-present
    / email-only / phone-only) so the friendly 409 fires before the DB
    constraint would. Scoped to ``id != candidate_id`` — re-saving a
    candidate's own unchanged identity is always fine.
    """
    if email is not None and phone_e164 is not None:
        match = sa.and_(
            sa.func.lower(Candidate.email) == email,
            Candidate.phone_e164 == phone_e164,
        )
    elif email is not None:
        match = sa.and_(
            sa.func.lower(Candidate.email) == email,
            Candidate.phone_e164.is_(None),
        )
    else:
        # phone-only (at least one is guaranteed present by the caller).
        match = sa.and_(
            Candidate.phone_e164 == phone_e164,
            Candidate.email.is_(None),
        )

    stmt = sa.select(Candidate.id).where(Candidate.id != candidate_id, match).limit(1)
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        raise CandidateIdentityConflictError(
            "Another candidate already uses this email / phone combination."
        )


async def update_application_candidate(
    session: AsyncSession,
    *,
    application_id: uuid.UUID,
    full_name: str,
    email: str | None,
    phone: str | None,
    actor: User,
    ip_address: str | None,
) -> Candidate:
    """Edit the candidate's contact details (name / email / phone) via an application.

    Allowed only while the application is in a :data:`CONTACT_EDITABLE_STAGES`
    stage (``vector_screen`` / ``hard_filter``); past that the WhatsApp /
    interview flow owns the contact channel, so we raise
    :class:`CandidateContactLockedError` (422).

    The candidate row is shared across all of that person's applications, so the
    edit is a single-source update — it propagates to every application they
    hold. The stage gate is evaluated on *this* application (the one HR is
    looking at).

    Validates + normalises the fields (email lower-cased, phone → E.164),
    enforces at-least-one-contact, and refuses an identity that already belongs
    to another candidate (:class:`CandidateIdentityConflictError`, 409). Writes
    a ``candidate_details_updated`` audit row against the application (so it
    surfaces in that application's activity feed) only when something actually
    changed, and invalidates the job's list cache (name / email are denormalised
    into the list). The route layer owns the commit.
    """
    app = await _load_application_for_update(session, application_id)

    if app.stage not in CONTACT_EDITABLE_STAGES:
        raise CandidateContactLockedError(
            f"Contact details are locked once the application reaches the "
            f"{app.stage.value!r} stage.",
            details={"stage": app.stage.value},
        )

    # Lock the candidate row too: two HR users editing the same person from
    # different applications would otherwise race on the before-state snapshot.
    cand_stmt = sa.select(Candidate).where(Candidate.id == app.candidate_id).with_for_update()
    candidate = (await session.execute(cand_stmt)).scalar_one_or_none()
    if candidate is None:
        raise ApplicationNotFoundError(f"Application {application_id} has a missing candidate row")

    new_name = full_name.strip()
    if not new_name:
        raise InvalidEmailError(
            "Full name is required.", error_code="invalid_full_name", http_status=400
        )
    new_email = _normalize_email(email)
    new_phone = _normalize_phone(phone)

    if new_email is None and new_phone is None:
        raise InvalidEmailError(
            "A candidate must have at least an email or a phone number.",
            error_code="contact_required",
            http_status=400,
        )

    before = {
        "full_name": candidate.full_name,
        "email": candidate.email,
        "phone_e164": candidate.phone_e164,
    }
    after = {"full_name": new_name, "email": new_email, "phone_e164": new_phone}

    if before == after:
        # No-op edit — don't spend an audit row or a cache flush on it.
        return candidate

    # Only run the (indexed) collision check when the identity pair actually
    # changes; a pure name edit can't collide.
    if (before["email"], before["phone_e164"]) != (new_email, new_phone):
        await _assert_identity_available(
            session,
            candidate_id=candidate.id,
            email=new_email,
            phone_e164=new_phone,
        )

    candidate.full_name = new_name
    candidate.email = new_email  # @validates lower-cases; already normalised
    candidate.phone_e164 = new_phone
    candidate.updated_at = datetime.now(UTC)

    session.add(
        AuditLog(
            user_id=actor.id,
            entity_type=AuditEntityType.APPLICATION.value,
            entity_id=app.id,
            action=AuditAction.CANDIDATE_DETAILS_UPDATED.value,
            before_state=before,
            after_state=after,
            ip_address=ip_address,
        )
    )
    await session.flush()

    await _invalidate_list_cache_for_job(app.job_id)
    # PII-safe: log only which fields changed, never the values.
    changed = [k for k in after if before[k] != after[k]]
    logger.info(
        "application.candidate_details_updated",
        extra={
            "application_id": str(app.id),
            "candidate_id": str(candidate.id),
            "user_id": str(actor.id),
            "changed_fields": changed,
        },
    )
    return candidate


# --- Interview feedback -----------------------------------------------------


async def update_interview_feedback(
    session: AsyncSession,
    *,
    application_id: uuid.UUID,
    score: float,
    comment: str | None,
    actor: User,
    ip_address: str | None,
) -> Application:
    """Record HR's post-interview evaluation (a [0, 100] mark + optional comment).

    Allowed only while the application is at the ``interview`` stage — the mark
    is a post-interview judgement, so any other stage is a client bug we surface
    as :class:`InterviewFeedbackStageError` (422). Re-submitting overwrites the
    prior value (HR correcting a mistyped mark). Writes an ``interview_scored``
    audit row (the mark only — the free-text comment is not copied into the
    audit trail) and stamps ``interview_scored_at``. The route owns the commit.
    """
    app = await _load_application_for_update(session, application_id)

    if app.stage is not ApplicationStage.INTERVIEW:
        raise InterviewFeedbackStageError(
            f"Interview feedback can only be recorded while the application is at "
            f"the 'interview' stage (currently {app.stage.value!r}).",
            details={"stage": app.stage.value},
        )

    before_score = app.interview_score
    new_comment = comment.strip() if comment and comment.strip() else None

    app.interview_score = score
    app.interview_comment = new_comment
    app.interview_scored_at = datetime.now(UTC)

    session.add(
        AuditLog(
            user_id=actor.id,
            entity_type=AuditEntityType.APPLICATION.value,
            entity_id=app.id,
            action=AuditAction.INTERVIEW_SCORED.value,
            before_state={"interview_score": before_score},
            after_state={"interview_score": score, "comment_set": new_comment is not None},
            ip_address=ip_address,
        )
    )
    await session.flush()

    # PII-safe: log the mark + who, never the free-text comment.
    logger.info(
        "application.interview_scored",
        extra={
            "application_id": str(app.id),
            "user_id": str(actor.id),
            "interview_score": score,
        },
    )
    return app
