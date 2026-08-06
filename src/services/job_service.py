"""Job CRUD service.

Pure async functions over an ``AsyncSession`` — the route layer owns
request/response shaping and ``commit()``; this module raises domain
exceptions on failure and uses ``flush()`` so route-level commits remain
the single source of transactional truth.

The ``get_job_detail`` path is wrapped by a 60-second Redis cache. Cache
keys include the row's ``updated_at`` epoch, so any write naturally
rotates the key — explicit invalidation isn't needed. Cache misses fall
through to the DB; cache backend failures degrade to direct reads with a
warning log (never a 500).

Audit log for status changes is structured-log-based for now — no
``audit_log`` table exists in this phase. ``job.status_changed`` carries
``{job_id, old, new, user_id}`` and is consumable by the central log
pipeline once it lands.

Background work (JD embedding + WhatsApp question generation) on the
``draft → open`` transition is **deferred to Step 2.6**. A TODO marks
the exact line where the Celery enqueue will go.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Final, cast

import redis.asyncio as aioredis
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models.application import Application
from src.db.models.job import Job
from src.db.models.public_upload_token import PublicUploadToken
from src.db.models.user import User
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.jobs import JobListOrder, JobStatus
from src.exceptions import IllegalStatusTransitionError, JobNotFoundError, SlugNotFoundError
from src.schemas.job import (
    JobCreateRequest,
    JobDetailResponse,
    PublicJobResponse,
    WhatsAppQuestionsUpdateRequest,
)
from src.schemas.whatsapp_question import WhatsAppQuestion
from src.utils.sanitization import strip_html
from src.utils.slug import generate_public_slug

logger = logging.getLogger(__name__)

# --- Cache constants ---------------------------------------------------------

JOB_DETAIL_CACHE_KEY_PREFIX: Final[str] = "job:detail:"
JOB_DETAIL_CACHE_TTL_SECONDS: Final[int] = 60

# --- Listing constants -------------------------------------------------------

DEFAULT_PAGE_SIZE: Final[int] = 20
MAX_PAGE_SIZE: Final[int] = 100

# --- Status transitions ------------------------------------------------------
# Lifecycle matrix. "Active" in the UI is ``OPEN``. ``INACTIVE`` is a
# reactivatable pause; ``ARCHIVED`` is the terminal "ended" state (still
# reopenable). ``CLOSED`` is the legacy terminal state, kept reachable for
# backward-compat. Anything else raises IllegalStatusTransitionError (HTTP 409).
_REACTIVATABLE: Final[frozenset[JobStatus]] = frozenset(
    {JobStatus.INACTIVE, JobStatus.ARCHIVED, JobStatus.CLOSED}
)
_ALLOWED_TRANSITIONS: Final[dict[JobStatus, frozenset[JobStatus]]] = {
    JobStatus.DRAFT: frozenset({JobStatus.OPEN, JobStatus.ARCHIVED}),
    JobStatus.OPEN: frozenset({JobStatus.INACTIVE, JobStatus.ARCHIVED, JobStatus.CLOSED}),
    JobStatus.INACTIVE: frozenset({JobStatus.OPEN, JobStatus.ARCHIVED}),
    JobStatus.ARCHIVED: frozenset({JobStatus.OPEN}),
    JobStatus.CLOSED: frozenset({JobStatus.OPEN, JobStatus.ARCHIVED}),
}
# Statuses that stamp ``closed_at`` (job ended). ``INACTIVE`` is a pause, not an
# end, so it leaves ``closed_at`` untouched.
_ENDING_STATUSES: Final[frozenset[JobStatus]] = frozenset({JobStatus.CLOSED, JobStatus.ARCHIVED})


def _redis_client() -> aioredis.Redis:
    """Build an async Redis client lazily.

    Mirrors the pattern in ``src/ai/claude.py`` — the client is cheap and
    connection pooling is owned by the redis lib, so re-instantiating per
    call is fine in tests but wasteful in production. Tests can override
    by patching ``job_service._redis_client``.
    """
    client = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, decode_responses=False
    )
    return cast(aioredis.Redis, client)


def _detail_cache_key(job_id: uuid.UUID, updated_at: datetime) -> str:
    """Cache key for a job's detail payload.

    Includes the ``updated_at`` epoch so a row UPDATE (which the DB
    trigger always rolls forward) rotates the key — no DELETE needed.
    """
    return f"{JOB_DETAIL_CACHE_KEY_PREFIX}{job_id}:v{int(updated_at.timestamp())}"


# --- Create ------------------------------------------------------------------


async def create_job(
    db: AsyncSession,
    *,
    request: JobCreateRequest,
    created_by: User,
) -> Job:
    """Persist a new draft job + its initial public-upload token.

    Job description is HTML-stripped before storage. The public slug
    lives in two places: ``jobs.public_slug`` (denormalised, returned in
    job responses) and ``public_upload_tokens.slug`` (source of truth
    the public ``/apply/{slug}`` route resolves against). Both get the
    same value at creation so the public link works immediately.

    Without the token row the public-apply route returns 410
    ``slug_inactive_or_unknown`` even though the job exists and is
    open — see the fix that introduced this insert.
    """
    slug = generate_public_slug()
    job = Job(
        created_by=created_by.id,
        title=request.title,
        hiring_company=request.hiring_company,
        country=request.country,
        city=request.city,
        employment_type=request.employment_type,
        work_mode=request.work_mode,
        currency=request.currency,
        min_salary=request.min_salary,
        max_salary=request.max_salary,
        notice_period=request.notice_period,
        min_experience_years=request.min_experience_years,
        required_skills=request.required_skills,
        preferred_skills=request.preferred_skills,
        visa_requirement=request.visa_requirement,
        nationality_preference=request.nationality_preference,
        languages_required=request.languages_required,
        job_description=strip_html(request.job_description),
        job_description_ar=(
            strip_html(request.job_description_ar)
            if request.job_description_ar is not None
            else None
        ),
        status=JobStatus.DRAFT,
        public_slug=slug,
    )
    db.add(job)
    await db.flush()

    db.add(PublicUploadToken(job_id=job.id, slug=slug, is_active=True))
    await db.flush()

    logger.info(
        "job.created",
        extra={"job_id": str(job.id), "created_by": str(created_by.id)},
    )
    return job


# --- Read --------------------------------------------------------------------


async def _load_job_or_404(db: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await db.get(Job, job_id)
    if job is None:
        raise JobNotFoundError(f"Job {job_id} not found")
    return job


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> Job:
    """Return the Job ORM row or raise :class:`JobNotFoundError`."""
    return await _load_job_or_404(db, job_id)


async def get_job_detail_cached(db: AsyncSession, job_id: uuid.UUID) -> JobDetailResponse:
    """Return the cached or freshly-queried detail payload for ``job_id``.

    The two-step "fetch row, then maybe hit cache" structure looks odd but
    is deliberate: the cache key contains ``updated_at``, and we don't
    know that without reading the row first. The DB query is cheap (PK
    lookup); the cache shaves off the ``model_validate`` cost for hot
    jobs and lets a horizontally scaled deployment reuse parsed payloads.

    A Redis outage is logged at WARNING but never propagated — we'd
    rather serve a slightly slower response than 500 the route.
    """
    job = await _load_job_or_404(db, job_id)
    key = _detail_cache_key(job.id, job.updated_at)

    redis = _redis_client()
    try:
        cached = await redis.get(key)
    except Exception:
        # Any redis failure (timeout, connection refused, decode error)
        # is treated as a cache miss — never propagate to the client.
        logger.warning("job_service.detail_cache.get_failed", extra={"job_id": str(job.id)})
        cached = None

    if cached is not None:
        decoded = cached.decode("utf-8") if isinstance(cached, bytes) else cached
        return JobDetailResponse.model_validate(json.loads(decoded))

    detail = JobDetailResponse.model_validate(job)
    try:
        await redis.set(key, detail.model_dump_json(), ex=JOB_DETAIL_CACHE_TTL_SECONDS)
    except Exception:
        # Same reasoning as the GET above — degraded caching is fine.
        logger.warning("job_service.detail_cache.set_failed", extra={"job_id": str(job.id)})

    return detail


async def get_public_job_by_slug(db: AsyncSession, slug: str) -> PublicJobResponse:
    """Return the candidate-facing job view for an active public slug.

    Resolves the slug against ``public_upload_tokens`` (the source of
    truth the public ``/apply/{slug}`` route also uses), not the
    denormalised ``jobs.public_slug``, so a rotated/deactivated link
    stops resolving here too. Unknown or inactive slugs raise
    :class:`SlugNotFoundError` (HTTP 410) — the same response for "never
    existed" and "deactivated" so live slugs can't be enumerated.

    The job's lifecycle ``status`` is returned as-is (a closed role still
    resolves); gating whether applications are accepted is the upload
    route's job, not this read's.
    """
    stmt = (
        sa.select(Job)
        .join(PublicUploadToken, PublicUploadToken.job_id == Job.id)
        .where(
            PublicUploadToken.slug == slug,
            PublicUploadToken.is_active.is_(True),
        )
    )
    job = (await db.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise SlugNotFoundError("Public upload slug is unknown or inactive.")
    return PublicJobResponse.model_validate(job)


# --- List --------------------------------------------------------------------


async def list_jobs(
    db: AsyncSession,
    *,
    status: JobStatus | None,
    search: str | None,
    page: int,
    page_size: int,
    order: JobListOrder = JobListOrder.UPDATED_AT,
) -> tuple[list[Job], int, dict[uuid.UUID, dict[str, int]]]:
    """Paginated list of jobs with optional ``status`` and ``search`` filters.

    ``search`` does a case-insensitive substring match against ``title``.
    Ordering is ``updated_at DESC`` by default so HR sees the most recently
    touched jobs first; pass ``order=JobListOrder.TITLE`` for a case-insensitive
    alphabetical sort — the canonical order for job-selector dropdowns, so every
    selector lists jobs identically. Either way we tiebreak by ``id`` for
    deterministic pagination.

    Returns ``(items, total, stage_counts)`` where ``stage_counts`` maps each
    listed job's id to its zero-filled ``{stage_value: count}`` funnel (see
    :func:`_stage_counts_for_jobs`).
    """
    page_size = min(page_size, MAX_PAGE_SIZE)
    offset = (page - 1) * page_size

    base = sa.select(Job)
    count_q = sa.select(sa.func.count()).select_from(Job)

    if status is not None:
        base = base.where(Job.status == status)
        count_q = count_q.where(Job.status == status)
    if search:
        pattern = f"%{search}%"
        base = base.where(Job.title.ilike(pattern))
        count_q = count_q.where(Job.title.ilike(pattern))

    primary_order = (
        sa.func.lower(Job.title).asc()
        if order is JobListOrder.TITLE
        else Job.updated_at.desc()
    )
    base = base.order_by(primary_order, Job.id).offset(offset).limit(page_size)

    items = list((await db.execute(base)).scalars().all())
    total_raw = (await db.execute(count_q)).scalar_one()
    total = int(total_raw or 0)

    stage_counts = await _stage_counts_for_jobs(db, [job.id for job in items])

    return items, total, stage_counts


async def _stage_counts_for_jobs(
    db: AsyncSession, job_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, int]]:
    """Per-job application count broken down by pipeline stage.

    One ``GROUP BY (job_id, stage)`` round-trip over only the jobs on the
    current page, then zero-filled across every :class:`ApplicationStage`
    value so each map is the full enum set (the API contract). Archived
    applications are excluded — moving a candidate to the talent pool
    soft-archives their application, and those pooled candidates are no
    longer in this job's pipeline, so the funnel matches the active
    pipeline count. Rejected/accepted rows are still counted: they
    preserve the stage they stopped at and remain part of the funnel.
    """
    zero_filled: dict[str, int] = {stage.value: 0 for stage in ApplicationStage}
    counts: dict[uuid.UUID, dict[str, int]] = {job_id: dict(zero_filled) for job_id in job_ids}
    if not job_ids:
        return counts

    result = await db.execute(
        sa.select(Application.job_id, Application.stage, sa.func.count())
        .where(
            Application.job_id.in_(job_ids),
            Application.status != ApplicationStatus.ARCHIVED,
        )
        .group_by(Application.job_id, Application.stage)
    )
    for job_id, stage, count in result.all():
        counts[job_id][stage.value] = int(count)

    return counts


# --- Status update -----------------------------------------------------------


async def update_status(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    new_status: JobStatus,
    actor: User,
) -> tuple[Job, bool]:
    """Apply a status transition, raising on disallowed edges.

    Returns ``(job, just_opened)`` where ``just_opened`` is True iff the
    transition landed on ``OPEN`` from a non-open state. The route layer
    uses that flag to dispatch the pipeline tasks **after** the
    transaction commits, so the worker never sees a row that isn't yet
    visible.
    """
    job = await _load_job_or_404(db, job_id)
    old_status = job.status
    allowed = _ALLOWED_TRANSITIONS.get(old_status, frozenset())

    if new_status not in allowed:
        raise IllegalStatusTransitionError(
            f"Cannot move job from {old_status.value!r} to {new_status.value!r}",
            details={"from": old_status.value, "to": new_status.value},
        )

    job.status = new_status
    if new_status in _ENDING_STATUSES:
        job.closed_at = datetime.now(UTC)
    elif new_status is JobStatus.OPEN and old_status in _REACTIVATABLE:
        # Reactivating from a paused/ended state clears the closed_at marker.
        job.closed_at = None

    await db.flush()
    logger.info(
        "job.status_changed",
        extra={
            "job_id": str(job.id),
            "old": old_status.value,
            "new": new_status.value,
            "user_id": str(actor.id),
        },
    )
    just_opened = new_status is JobStatus.OPEN and old_status is not JobStatus.OPEN
    return job, just_opened


# --- WhatsApp questions ------------------------------------------------------


async def get_whatsapp_questions(db: AsyncSession, job_id: uuid.UUID) -> list[WhatsAppQuestion]:
    job = await _load_job_or_404(db, job_id)
    return [WhatsAppQuestion.model_validate(q) for q in job.whatsapp_questions]


async def replace_whatsapp_questions(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    request: WhatsAppQuestionsUpdateRequest,
    actor: User,
) -> list[WhatsAppQuestion]:
    """Overwrite the ``whatsapp_questions`` JSONB with the validated list."""
    job = await _load_job_or_404(db, job_id)
    serialized: list[dict[str, Any]] = [q.model_dump(mode="json") for q in request.questions]
    job.whatsapp_questions = serialized
    await db.flush()
    logger.info(
        "job.whatsapp_questions.updated",
        extra={
            "job_id": str(job.id),
            "count": len(serialized),
            "user_id": str(actor.id),
        },
    )
    return list(request.questions)
