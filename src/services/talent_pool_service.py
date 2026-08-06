"""Talent-pool service — add, list, and source pooled candidates.

Pure async functions over an ``AsyncSession``; domain exceptions on failure;
``flush()`` not ``commit()`` so the route owns the transaction (and, for
sourcing, dispatches the CV pipeline *after* it commits — same
dispatch-after-commit discipline as bulk upload).

Sourcing creates a **fresh application at L1** (``vector_screen``) pointing at
the candidate's current CV and flags it ``sourced_from_talent_pool`` — the
scoring pipeline then runs normally against that job; only the eventual L2
WhatsApp greeting differs. Idempotent on the ``(candidate, job)`` unique
constraint: an existing application is returned untouched.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.ai.contact_extractor import ContactDetails, extract_contact_details
from src.config import settings
from src.db.models.application import Application
from src.db.models.application_score import ApplicationScore
from src.db.models.audit_log import AuditLog
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.job import Job
from src.db.models.talent_pool_entry import TalentPoolEntry
from src.db.models.user import User
from src.db.models.whatsapp import WhatsAppConversation, WhatsAppMessage
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.audit import AuditAction, AuditEntityType
from src.enums.jobs import JobStatus
from src.enums.thresholds import TALENT_POOL_SEARCH_MIN_SIMILARITY
from src.enums.whatsapp import WhatsAppDirection
from src.exceptions import (
    ApplicationNotFoundError,
    CandidateHasNoCvError,
    CandidateNotFoundError,
    CandidateNotInTalentPoolError,
    CvParseNoContactError,
    JobNotEmbeddedError,
    JobNotFoundError,
    JobNotOpenForSourcingError,
    SearchQueryRequiredError,
)
from src.integrations.azure_blob import UploadedBlob, upload_pdf
from src.schemas.application import ApplicationScoreResponse, CandidateNested
from src.schemas.talent_pool import (
    CandidateHistoryResponse,
    CandidateHistoryScreening,
    CandidateHistoryScreeningAnswer,
    CandidateHistoryStint,
    TalentPoolCandidateNested,
    TalentPoolEntryResponse,
    TalentPoolListResponse,
    TalentPoolSearchResponse,
    TalentPoolSearchResultItem,
)
from src.utils.pdf import extract_page1_signals, extract_text
from src.utils.percentage import format_percent_optional
from src.utils.phone import InvalidPhoneNumberError, normalize_phone

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE: Final[int] = 20
MAX_PAGE_SIZE: Final[int] = 100

# Semantic-search result-count bounds.
DEFAULT_SEARCH_LIMIT: Final[int] = 10
MAX_SEARCH_LIMIT: Final[int] = 50

# How many parsed skills to surface on a pool candidate snapshot.
TOP_SKILLS_COUNT: Final[int] = 6

# Semantic-search relevance gate. Candidates whose cosine *distance* exceeds this
# are too far from the JD to be a relevant search hit and are dropped from
# results. Stricter than the CV pipeline's ingestion auto-reject floor:
# ``TALENT_POOL_SEARCH_MIN_SIMILARITY`` is a 0-100 percent; cosine_distance is
# 0-2, and distance = 1 - similarity, hence (100 - x) / 100.
_RELEVANCE_MAX_DISTANCE: Final[float] = (100.0 - TALENT_POOL_SEARCH_MIN_SIMILARITY) / 100.0

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_SEARCH_LIMIT",
    "MAX_PAGE_SIZE",
    "MAX_SEARCH_LIMIT",
    "PoolUploadResult",
    "add_to_pool",
    "get_candidate_history",
    "list_pool",
    "move_application_to_pool",
    "search_pool",
    "source_to_job",
    "upload_to_pool",
]


@dataclass(frozen=True, slots=True)
class PoolUploadResult:
    """Outcome of a direct talent-pool CV upload.

    ``needs_pipeline`` is True when the candidate's current CV still lacks an
    embedding — the route uses it to decide whether to dispatch the (parse +
    authenticity + embed) pipeline after commit.
    """

    entry: TalentPoolEntry
    candidate: Candidate
    cv_document_id: uuid.UUID
    candidate_created: bool
    cv_created: bool
    needs_pipeline: bool


def _expiry_from(now: datetime) -> datetime:
    return now + timedelta(days=settings.talent_pool_expiry_days)


async def _get_candidate_or_404(session: AsyncSession, candidate_id: uuid.UUID) -> Candidate:
    candidate = await session.get(Candidate, candidate_id)
    if candidate is None:
        raise CandidateNotFoundError(f"Candidate {candidate_id} not found")
    return candidate


def _current_role(profile: dict[str, Any]) -> str | None:
    """Most-recent job title from a parsed CV's work history.

    Prefers the entry still marked current (``end is None``); otherwise the
    first (CVs list newest-first). Returns ``None`` when the CV has no usable
    work history yet.
    """
    history = profile.get("work_history")
    if not isinstance(history, list):
        return None
    entries = [w for w in history if isinstance(w, dict)]
    current = next((w for w in entries if w.get("end") is None), None)
    entry = current or (entries[0] if entries else None)
    if entry is None:
        return None
    title = entry.get("title")
    return title.strip() if isinstance(title, str) and title.strip() else None


def _top_skills(profile: dict[str, Any]) -> list[str]:
    """First :data:`TOP_SKILLS_COUNT` string skills from a parsed CV."""
    skills = profile.get("skills")
    if not isinstance(skills, list):
        return []
    return [s for s in skills if isinstance(s, str)][:TOP_SKILLS_COUNT]


def _candidate_nested(candidate: Candidate) -> TalentPoolCandidateNested:
    """Build the enriched candidate snapshot (role/skills/authenticity)."""
    profile = candidate.parsed_profile or {}
    return TalentPoolCandidateNested(
        id=candidate.id,
        full_name=candidate.full_name,
        email=candidate.email,
        phone_e164=candidate.phone_e164,
        role=_current_role(profile),
        skills=_top_skills(profile),
        authenticity_score=candidate.authenticity_score,
        authenticity_band=(
            candidate.authenticity_band.value if candidate.authenticity_band is not None else None
        ),
    )


async def add_to_pool(
    session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    source_job_id: uuid.UUID | None,
    actor: User,
    ip_address: str | None,
) -> TalentPoolEntry:
    """Add a candidate to the talent pool (idempotent on the candidate).

    A candidate has at most one entry (UNIQUE ``candidate_id``). If an active
    entry already exists it's returned unchanged; a previously-deactivated or
    expired one is revived with a fresh expiry. ``source_job_id`` (where the
    candidate came from) is validated when provided.
    """
    await _get_candidate_or_404(session, candidate_id)
    if source_job_id is not None and await session.get(Job, source_job_id) is None:
        raise JobNotFoundError(f"Job {source_job_id} not found")

    now = datetime.now(UTC)
    entry = (
        await session.execute(
            sa.select(TalentPoolEntry)
            .where(TalentPoolEntry.candidate_id == candidate_id)
            .with_for_update()
        )
    ).scalar_one_or_none()

    if entry is not None and entry.is_active and entry.expires_at > now:
        logger.info(
            "talent_pool.add.already_present",
            extra={"candidate_id": str(candidate_id), "entry_id": str(entry.id)},
        )
        return entry

    if entry is not None:
        # Revive an inactive / expired entry rather than violating the UNIQUE.
        entry.is_active = True
        entry.expires_at = _expiry_from(now)
        entry.added_by = actor.id
        entry.added_at = now
        if source_job_id is not None:
            entry.source_job_id = source_job_id
    else:
        entry = TalentPoolEntry(
            candidate_id=candidate_id,
            source_job_id=source_job_id,
            added_by=actor.id,
            added_at=now,
            expires_at=_expiry_from(now),
            is_active=True,
        )
        session.add(entry)

    session.add(
        AuditLog(
            user_id=actor.id,
            entity_type=AuditEntityType.CANDIDATE.value,
            entity_id=candidate_id,
            action=AuditAction.TALENT_POOL_ADDED.value,
            before_state=None,
            after_state={
                "source_job_id": str(source_job_id) if source_job_id else None,
                "expires_at": entry.expires_at.isoformat(),
            },
            ip_address=ip_address,
        )
    )
    await session.flush()

    logger.info(
        "talent_pool.add.ok",
        extra={
            "candidate_id": str(candidate_id),
            "entry_id": str(entry.id),
            "actor_user_id": str(actor.id),
        },
    )
    return entry


async def list_pool(
    session: AsyncSession,
    *,
    active_only: bool,
    page: int,
    page_size: int,
) -> TalentPoolListResponse:
    """List talent-pool entries (newest first) with candidate snapshots."""
    source_job = aliased(Job)
    base = (
        sa.select(TalentPoolEntry, Candidate, source_job.title)
        .join(Candidate, Candidate.id == TalentPoolEntry.candidate_id)
        .outerjoin(source_job, source_job.id == TalentPoolEntry.source_job_id)
    )
    if active_only:
        base = base.where(TalentPoolEntry.is_active.is_(True))

    total = (
        await session.execute(
            sa.select(sa.func.count()).select_from(base.order_by(None).subquery())
        )
    ).scalar_one()

    rows = (
        await session.execute(
            base.order_by(TalentPoolEntry.added_at.desc(), TalentPoolEntry.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    items = [
        TalentPoolEntryResponse(
            id=entry.id,
            candidate_id=entry.candidate_id,
            source_job_id=entry.source_job_id,
            source_job_title=source_title,
            added_by=entry.added_by,
            added_at=entry.added_at,
            expires_at=entry.expires_at,
            is_active=entry.is_active,
            candidate=_candidate_nested(candidate),
        )
        for entry, candidate, source_title in rows
    ]
    return TalentPoolListResponse(items=items, total=int(total))


async def source_to_job(
    session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    job_id: uuid.UUID,
    actor: User,
    ip_address: str | None,
) -> tuple[Application, bool]:
    """Create a fresh application sourcing a pooled candidate onto a job.

    Returns ``(application, already_existed)``. When the candidate already has
    an application on this job, that row is returned untouched with
    ``already_existed=True`` (no new application, no pipeline) — the route uses
    this to decide whether to dispatch the CV pipeline.

    Raises if the candidate is missing, not in the active pool, has no current
    CV, or the job is missing / not open.
    """
    await _get_candidate_or_404(session, candidate_id)

    now = datetime.now(UTC)
    entry = (
        await session.execute(
            sa.select(TalentPoolEntry).where(
                TalentPoolEntry.candidate_id == candidate_id,
                TalentPoolEntry.is_active.is_(True),
                TalentPoolEntry.expires_at > now,
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        raise CandidateNotInTalentPoolError(
            f"Candidate {candidate_id} is not in the active talent pool"
        )

    job = await session.get(Job, job_id)
    if job is None:
        raise JobNotFoundError(f"Job {job_id} not found")
    if job.status is not JobStatus.OPEN:
        raise JobNotOpenForSourcingError(
            f"Job {job_id} is not open for applications (status={job.status.value})",
            details={"job_id": str(job_id), "status": job.status.value},
        )

    existing = (
        await session.execute(
            sa.select(Application).where(
                Application.candidate_id == candidate_id,
                Application.job_id == job_id,
                # A prior archived stint on this job doesn't block re-sourcing
                # — only a live application does. The candidate gets a fresh
                # stint while the archived one survives as history.
                Application.status != ApplicationStatus.ARCHIVED,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Self-heal the inconsistent state: a candidate who is both pooled and
        # already on this job leaves the pool (sourcing is a move, not a copy).
        entry.is_active = False
        await session.flush()
        logger.info(
            "talent_pool.source.already_applied",
            extra={"candidate_id": str(candidate_id), "application_id": str(existing.id)},
        )
        return existing, True

    cv_document = (
        await session.execute(
            sa.select(CvDocument).where(
                CvDocument.candidate_id == candidate_id,
                CvDocument.is_current.is_(True),
            )
        )
    ).scalar_one_or_none()
    if cv_document is None:
        raise CandidateHasNoCvError(f"Candidate {candidate_id} has no current CV to source")

    application = Application(
        candidate_id=candidate_id,
        job_id=job.id,
        cv_document_id=cv_document.id,
        stage=ApplicationStage.VECTOR_SCREEN,
        status=ApplicationStatus.ACTIVE,
        sourced_from_talent_pool=True,
        consent_context={
            "actor_user_id": str(actor.id),
            "actor_email": actor.email,
            "ip": ip_address or "unknown",
            "accepted_at": now.isoformat(),
            "source": "talent_pool_sourcing",
        },
        consented_at=now,
    )
    session.add(application)

    # Sourcing is a move, not a copy: the candidate leaves the pool and lives
    # only in this job's pipeline now (revivable later via move-to-pool).
    entry.is_active = False
    await session.flush()

    session.add(
        AuditLog(
            user_id=actor.id,
            entity_type=AuditEntityType.APPLICATION.value,
            entity_id=application.id,
            action=AuditAction.TALENT_POOL_SOURCED.value,
            before_state=None,
            after_state={
                "candidate_id": str(candidate_id),
                "job_id": str(job_id),
                "source": "talent_pool_sourcing",
                "removed_from_pool": True,
            },
            ip_address=ip_address,
        )
    )
    await session.flush()

    logger.info(
        "talent_pool.source.ok",
        extra={
            "candidate_id": str(candidate_id),
            "job_id": str(job_id),
            "application_id": str(application.id),
            "actor_user_id": str(actor.id),
        },
    )
    return application, False


async def move_application_to_pool(
    session: AsyncSession,
    *,
    application_id: uuid.UUID,
    actor: User,
    ip_address: str | None,
    reason: str | None = None,
) -> TalentPoolEntry:
    """Move a candidate out of a job's pipeline back into the talent pool.

    **Soft-archives** the application (``status=ARCHIVED``, ``archived_at`` set)
    instead of deleting it, so its ``application_scores`` and WhatsApp transcript
    are retained as the candidate's cross-job history. The stage is preserved so
    HR sees *where* the stint ended. Then (re-)adds the candidate to the pool.

    Only *this* application is archived — other jobs the candidate is on are
    untouched. Archived rows are excluded from the live ``(candidate, job)``
    uniqueness rule, so the candidate can later be sourced back onto the same
    job for a fresh stint while this one survives as history.

    Idempotent on an already-archived application: returns the (re-added) pool
    entry without re-stamping ``archived_at``.

    Raises :class:`ApplicationNotFoundError` if the application doesn't exist.
    """
    application = await session.get(Application, application_id)
    if application is None:
        raise ApplicationNotFoundError(f"Application {application_id} not found")

    candidate_id = application.candidate_id
    job_id = application.job_id
    # Normalize an all-whitespace reason to "no reason".
    cleaned_reason = reason.strip() if reason and reason.strip() else None

    if application.status is not ApplicationStatus.ARCHIVED:
        after_state: dict[str, str] = {
            "candidate_id": str(candidate_id),
            "job_id": str(job_id),
            "status": ApplicationStatus.ARCHIVED.value,
        }
        if cleaned_reason:
            after_state["reason"] = cleaned_reason
        session.add(
            AuditLog(
                user_id=actor.id,
                entity_type=AuditEntityType.APPLICATION.value,
                entity_id=application_id,
                action=AuditAction.APPLICATION_MOVED_TO_POOL.value,
                before_state={
                    "stage": application.stage.value,
                    "status": application.status.value,
                },
                after_state=after_state,
                ip_address=ip_address,
            )
        )
        # Archive, don't delete: scores + WhatsApp survive as history. Stage is
        # left untouched so HR sees how far the candidate got on this job. The
        # optional reason is stored against this stint for the cross-job history.
        application.status = ApplicationStatus.ARCHIVED
        application.archived_at = datetime.now(UTC)
        application.move_to_pool_reason = cleaned_reason
        await session.flush()

        # Drop the job's list cache so the now-archived stint disappears from
        # the pipeline immediately, matching every other application mutation.
        # Local import avoids a module-level cycle with application_service.
        from src.services.application_service import _invalidate_list_cache_for_job

        await _invalidate_list_cache_for_job(job_id)

    entry = await add_to_pool(
        session,
        candidate_id=candidate_id,
        source_job_id=job_id,
        actor=actor,
        ip_address=ip_address,
    )

    logger.info(
        "talent_pool.move_to_pool.ok",
        extra={
            "candidate_id": str(candidate_id),
            "job_id": str(job_id),
            "application_id": str(application_id),
            "actor_user_id": str(actor.id),
        },
    )
    return entry


def _build_screening_summary(
    conversation: WhatsAppConversation,
    messages: list[WhatsAppMessage],
) -> CandidateHistoryScreening:
    """Digest one stint's WhatsApp conversation into scored Q&A.

    Merges the conversation's structured ``answers`` copy (question + answer
    text, in ask order) with the per-answer scores carried on the inbound
    message rows (keyed by ``question_index``). The union of both index sets is
    walked so a question that was asked-but-unanswered (message, no answer copy)
    or answered-but-unscored still appears.
    """
    answers_copy = conversation.answers or []
    scored_by_index = {
        m.question_index: m
        for m in messages
        if m.direction is WhatsAppDirection.INBOUND and m.question_index is not None
    }

    indices = sorted(set(range(len(answers_copy))) | set(scored_by_index))
    answers: list[CandidateHistoryScreeningAnswer] = []
    for i in indices:
        copy = answers_copy[i] if i < len(answers_copy) else {}
        msg = scored_by_index.get(i)
        answers.append(
            CandidateHistoryScreeningAnswer(
                question_index=i,
                question=copy.get("question"),
                answer=copy.get("answer") or (msg.body if msg is not None else None),
                relevance_score=msg.answer_relevance_score if msg is not None else None,
                ai_score=msg.answer_ai_score if msg is not None else None,
                rationale=msg.answer_score_rationale if msg is not None else None,
            )
        )

    return CandidateHistoryScreening(
        conversation_id=conversation.id,
        state=conversation.state,
        answers=answers,
        created_at=conversation.created_at,
        closed_at=conversation.closed_at,
    )


async def get_candidate_history(
    session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
) -> CandidateHistoryResponse:
    """Assemble a candidate's entire cross-job journey for the pool view.

    One stint per application the candidate ever had — live, rejected,
    accepted, or archived (moved back to the pool) — each carrying the scores
    and WhatsApp screening computed against *that* job. Authenticity is
    candidate-level (shared across stints) so it lives once on the candidate
    snapshot, not per stint.

    Bounded query cost: the application rows plus three batched fan-out reads
    (jobs, ``application_scores``, WhatsApp conversations + messages) keyed on
    the candidate's application id set — no per-stint round-trips.

    Raises :class:`CandidateNotFoundError` if the candidate doesn't exist.
    """
    candidate = await _get_candidate_or_404(session, candidate_id)

    apps = list(
        (
            await session.execute(
                sa.select(Application)
                .where(Application.candidate_id == candidate_id)
                .order_by(Application.created_at.desc(), Application.id.asc())
            )
        )
        .scalars()
        .all()
    )

    app_ids = [a.id for a in apps]
    job_ids = {a.job_id for a in apps}

    # Job titles (one batched read).
    titles: dict[uuid.UUID, str] = {}
    if job_ids:
        title_rows = await session.execute(sa.select(Job.id, Job.title).where(Job.id.in_(job_ids)))
        titles = {jid: title for jid, title in title_rows.all()}

    # Score rows grouped per application (one batched read, newest-first).
    scores_by_app: dict[uuid.UUID, list[ApplicationScore]] = {aid: [] for aid in app_ids}
    if app_ids:
        score_rows = (
            (
                await session.execute(
                    sa.select(ApplicationScore)
                    .where(ApplicationScore.application_id.in_(app_ids))
                    .order_by(
                        ApplicationScore.computed_at.desc(),
                        ApplicationScore.id.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        for s in score_rows:
            scores_by_app[s.application_id].append(s)

    # WhatsApp conversations + their messages (two batched reads).
    convs_by_app: dict[uuid.UUID, WhatsAppConversation] = {}
    msgs_by_conv: dict[uuid.UUID, list[WhatsAppMessage]] = {}
    if app_ids:
        conv_rows = (
            (
                await session.execute(
                    sa.select(WhatsAppConversation).where(
                        WhatsAppConversation.application_id.in_(app_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        convs_by_app = {c.application_id: c for c in conv_rows}
        conv_ids = [c.id for c in conv_rows]
        if conv_ids:
            msg_rows = (
                (
                    await session.execute(
                        sa.select(WhatsAppMessage)
                        .where(WhatsAppMessage.conversation_id.in_(conv_ids))
                        .order_by(
                            WhatsAppMessage.created_at.asc(),
                            WhatsAppMessage.id.asc(),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for m in msg_rows:
                msgs_by_conv.setdefault(m.conversation_id, []).append(m)

    stints: list[CandidateHistoryStint] = []
    for app in apps:
        conv = convs_by_app.get(app.id)
        screening = (
            _build_screening_summary(conv, msgs_by_conv.get(conv.id, []))
            if conv is not None
            else None
        )
        stints.append(
            CandidateHistoryStint(
                application_id=app.id,
                job_id=app.job_id,
                job_title=titles.get(app.job_id, ""),
                stage=app.stage,
                status=app.status,
                sourced_from_talent_pool=app.sourced_from_talent_pool,
                similarity_score=format_percent_optional(app.similarity_score),
                hard_filter_score=format_percent_optional(app.hard_filter_score),
                hard_filter_breakdown=app.hard_filter_breakdown,
                scores=[ApplicationScoreResponse.model_validate(s) for s in scores_by_app[app.id]],
                screening=screening,
                move_to_pool_reason=app.move_to_pool_reason,
                created_at=app.created_at,
                stage_updated_at=app.stage_updated_at,
                archived_at=app.archived_at,
            )
        )

    now = datetime.now(UTC)
    in_pool = (
        await session.execute(
            sa.select(TalentPoolEntry.id).where(
                TalentPoolEntry.candidate_id == candidate_id,
                TalentPoolEntry.is_active.is_(True),
                TalentPoolEntry.expires_at > now,
            )
        )
    ).scalar_one_or_none() is not None

    logger.info(
        "talent_pool.history.ok",
        extra={"candidate_id": str(candidate_id), "stint_count": len(stints)},
    )
    return CandidateHistoryResponse(
        candidate=CandidateNested.model_validate(candidate),
        in_pool=in_pool,
        stints=stints,
        total_stints=len(stints),
    )


# ---------------------------------------------------------------------------
# Direct CV upload (Phase 7)
# ---------------------------------------------------------------------------


async def _find_or_create_candidate(
    session: AsyncSession,
    *,
    contact: ContactDetails,
) -> tuple[Candidate, bool]:
    """Resolve a candidate from extracted contact details (creating if new).

    Identity matches on whichever of email/phone are present and requires the
    absent one to be NULL — mirroring the bulk-upload intake and the three
    partial UNIQUE indexes from migration ``0013``. Returns
    ``(candidate, created)``. The caller has already gated that ``full_name``
    plus at least one contact field is present.
    """
    email = contact.email
    phone_e164: str | None = None
    if contact.phone is not None:
        try:
            phone_e164 = normalize_phone(contact.phone)
        except InvalidPhoneNumberError:
            phone_e164 = None

    conditions = [
        sa.func.lower(Candidate.email) == email.lower()
        if email is not None
        else Candidate.email.is_(None),
        Candidate.phone_e164 == phone_e164
        if phone_e164 is not None
        else Candidate.phone_e164.is_(None),
    ]
    existing = (await session.execute(sa.select(Candidate).where(*conditions))).scalar_one_or_none()
    if existing is not None:
        return existing, False

    candidate = Candidate(
        email=email,
        phone_e164=phone_e164,
        full_name=contact.full_name or "",
    )
    session.add(candidate)
    await session.flush()
    return candidate, True


async def _find_or_create_cv_document(
    session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    blob: UploadedBlob,
) -> tuple[CvDocument, bool]:
    """Reuse the candidate's CV with this sha256 or insert a new current one.

    Demotes any other ``is_current`` CV before inserting (the partial UNIQUE on
    ``cv_documents(candidate_id) WHERE is_current`` allows only one). Returns
    ``(cv_document, created)``.
    """
    existing = (
        await session.execute(
            sa.select(CvDocument).where(
                CvDocument.candidate_id == candidate_id,
                CvDocument.blob_sha256 == blob.sha256,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    await session.execute(
        sa.update(CvDocument)
        .where(CvDocument.candidate_id == candidate_id, CvDocument.is_current.is_(True))
        .values(is_current=False)
    )
    cv_doc = CvDocument(
        candidate_id=candidate_id,
        blob_url=blob.url,
        blob_sha256=blob.sha256,
        is_current=True,
    )
    session.add(cv_doc)
    await session.flush()
    return cv_doc, True


async def upload_to_pool(
    session: AsyncSession,
    *,
    pdf_bytes: bytes,
    actor: User,
    ip_address: str | None,
) -> PoolUploadResult:
    """Upload a CV directly into the talent pool (no job, no application).

    Extracts the candidate's identity from the CV (cached pypdf text + cached
    contact extractor), find-or-creates the candidate and CV document, uploads
    the PDF to Azure under the real candidate id, and adds the candidate to the
    pool. The route commits, then dispatches the parse/authenticity/embed
    pipeline so the CV becomes searchable.

    Raises :class:`CvParseNoContactError` when the CV yields no name plus at
    least one contact field — there's no other candidate to attach the CV to.
    """
    # Extract text (Redis-cached on sha256) then identity (Claude cached too).
    # Page-1 signals give the size-ranked name (over a job-title banner) and
    # de-glued text (so a label-glued email reads cleanly). Bytes never logged.
    text = await extract_text(pdf_bytes)
    signals = await extract_page1_signals(pdf_bytes)
    contact = await extract_contact_details(
        text,
        name_candidates=signals.name_candidates,
        contact_block_text=signals.deglued_text,
    )

    phone_present = False
    if contact.phone is not None:
        try:
            normalize_phone(contact.phone)
            phone_present = True
        except InvalidPhoneNumberError:
            phone_present = False
    if contact.full_name is None or (contact.email is None and not phone_present):
        raise CvParseNoContactError(
            "Could not extract a name plus an email or phone from the uploaded CV."
        )

    candidate, candidate_created = await _find_or_create_candidate(session, contact=contact)

    # Upload now that the real candidate id is known so the blob lands at
    # cvs/{candidate_id}/{sha256}.pdf — exactly where the CV pipeline fetches
    # it. upload_pdf is idempotent (overwrite=True).
    blob = await upload_pdf(pdf_bytes, candidate_id=candidate.id)
    cv_document, cv_created = await _find_or_create_cv_document(
        session, candidate_id=candidate.id, blob=blob
    )

    entry = await add_to_pool(
        session,
        candidate_id=candidate.id,
        source_job_id=None,
        actor=actor,
        ip_address=ip_address,
    )

    needs_pipeline = cv_document.cv_embedding is None
    logger.info(
        "talent_pool.upload.ok",
        extra={
            "candidate_id": str(candidate.id),
            "cv_document_id": str(cv_document.id),
            "candidate_created": candidate_created,
            "cv_created": cv_created,
            "needs_pipeline": needs_pipeline,
            "actor_user_id": str(actor.id),
        },
    )
    return PoolUploadResult(
        entry=entry,
        candidate=candidate,
        cv_document_id=cv_document.id,
        candidate_created=candidate_created,
        cv_created=cv_created,
        needs_pipeline=needs_pipeline,
    )


def _enqueue_pool_cv_pipeline(cv_document_id: uuid.UUID, candidate_id: uuid.UUID) -> None:
    """Dispatch the pool CV pipeline (parse + authenticity + embed) after commit.

    Lives in a top-level function so tests can monkey-patch it without touching
    Celery's broker config; imports inside to keep Celery init out of module
    import.
    """
    from src.workers.tasks.cv_pipeline import dispatch_pool_cv_processing_task

    dispatch_pool_cv_processing_task.delay(str(cv_document_id), str(candidate_id))


# ---------------------------------------------------------------------------
# Semantic search (Phase 7)
# ---------------------------------------------------------------------------


def _pool_search_item(
    entry: TalentPoolEntry,
    candidate: Candidate,
    cv_document_id: uuid.UUID,
    source_title: str | None,
    similarity: float | None,
) -> TalentPoolSearchResultItem:
    """Build one search hit. ``similarity`` is None for lexical (text) hits."""
    return TalentPoolSearchResultItem(
        candidate_id=entry.candidate_id,
        entry_id=entry.id,
        cv_document_id=cv_document_id,
        similarity_score=similarity,
        source_job_title=source_title,
        added_at=entry.added_at,
        expires_at=entry.expires_at,
        is_active=entry.is_active,
        candidate=_candidate_nested(candidate),
    )


async def search_pool(
    session: AsyncSession,
    *,
    query: str | None,
    job_id: uuid.UUID | None,
    limit: int,
    active_only: bool,
) -> TalentPoolSearchResponse:
    """Find pooled candidates two complementary ways.

    * **``job_id`` → semantic.** Rank by cosine similarity of each candidate's
      current CV against the job's stored JD embedding (full JD vs full CV is
      where embeddings are reliable), gated to the relevant ones
      (``TALENT_POOL_SEARCH_MIN_SIMILARITY`` - a stricter bar than the CV
      pipeline's ingestion auto-reject floor). Hits carry a 0-100
      ``similarity_score``; the response echoes the
      job title as ``query``.
    * **``query`` → lexical.** A short role/skill phrase is matched literally
      against the candidate's name, parsed work-history titles, and skills — so
      "software engineer" returns the software engineers, not a noisy embedding
      ranking. Hits have ``similarity_score = None`` and are ordered newest-first.

    Exactly one input is needed (``job_id`` wins if both are passed):

    * neither → :class:`SearchQueryRequiredError` (422)
    * ``job_id`` for an unknown job → :class:`JobNotFoundError` (404)
    * ``job_id`` for a job not embedded yet → :class:`JobNotEmbeddedError` (409)
    """
    if job_id is not None:
        return await _search_pool_by_job(
            session, job_id=job_id, limit=limit, active_only=active_only
        )
    if query is not None:
        return await _search_pool_by_text(
            session, query=query, limit=limit, active_only=active_only
        )
    raise SearchQueryRequiredError("Provide either a text query or a job_id to search.")


async def _search_pool_by_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    limit: int,
    active_only: bool,
) -> TalentPoolSearchResponse:
    """Semantic search: rank the pool by cosine vs the job's JD embedding."""
    job = await session.get(Job, job_id)
    if job is None:
        raise JobNotFoundError(f"Job {job_id} not found")
    if job.jd_embedding is None:
        raise JobNotEmbeddedError(
            f"Job {job_id} has no JD embedding yet",
            details={"job_id": str(job_id)},
        )

    source_job = aliased(Job)
    distance = CvDocument.cv_embedding.cosine_distance(job.jd_embedding)
    stmt = (
        sa.select(
            TalentPoolEntry,
            Candidate,
            CvDocument.id,
            distance.label("distance"),
            source_job.title,
        )
        .join(Candidate, Candidate.id == TalentPoolEntry.candidate_id)
        .join(
            CvDocument,
            sa.and_(CvDocument.candidate_id == Candidate.id, CvDocument.is_current.is_(True)),
        )
        .outerjoin(source_job, source_job.id == TalentPoolEntry.source_job_id)
        .where(CvDocument.cv_embedding.is_not(None))
        # Relevance gate: drop candidates below the same similarity cutoff the CV
        # pipeline auto-rejects below. cosine_distance is 0-2; threshold a 0-100 %.
        .where(distance <= _RELEVANCE_MAX_DISTANCE)
        .order_by(distance.asc())
        .limit(limit)
    )
    if active_only:
        stmt = stmt.where(
            TalentPoolEntry.is_active.is_(True),
            TalentPoolEntry.expires_at > datetime.now(UTC),
        )

    rows = (await session.execute(stmt)).all()
    items = [
        # cosine_distance = 1 - cosine_similarity; rescale to a 0-100 percentage.
        _pool_search_item(
            entry, candidate, cv_id, source_title, round((1.0 - float(dist)) * 100.0, 2)
        )
        for entry, candidate, cv_id, dist, source_title in rows
    ]
    logger.info(
        "talent_pool.search.ok",
        extra={
            "result_count": len(items),
            "limit": limit,
            "active_only": active_only,
            "mode": "job",
        },
    )
    return TalentPoolSearchResponse(query=job.title, items=items, total=len(items))


async def _search_pool_by_text(
    session: AsyncSession,
    *,
    query: str,
    limit: int,
    active_only: bool,
) -> TalentPoolSearchResponse:
    """Lexical search: match the phrase against name, parsed role titles, skills."""
    pattern = f"%{query}%"
    # Substring-match the phrase against the candidate's name and the JSON text of
    # their work history (role titles / companies) and skills. Casting the JSONB
    # sub-objects to text keeps this a per-row column predicate (no lateral
    # correlation), and a missing key casts to NULL → simply doesn't match.
    work_history_text = sa.cast(Candidate.parsed_profile["work_history"], sa.Text)
    skills_text = sa.cast(Candidate.parsed_profile["skills"], sa.Text)

    source_job = aliased(Job)
    stmt = (
        sa.select(TalentPoolEntry, Candidate, CvDocument.id, source_job.title)
        .join(Candidate, Candidate.id == TalentPoolEntry.candidate_id)
        .join(
            CvDocument,
            sa.and_(CvDocument.candidate_id == Candidate.id, CvDocument.is_current.is_(True)),
        )
        .outerjoin(source_job, source_job.id == TalentPoolEntry.source_job_id)
        .where(
            sa.or_(
                Candidate.full_name.ilike(pattern),
                work_history_text.ilike(pattern),
                skills_text.ilike(pattern),
            )
        )
        .order_by(TalentPoolEntry.added_at.desc(), TalentPoolEntry.id.asc())
        .limit(limit)
    )
    if active_only:
        stmt = stmt.where(
            TalentPoolEntry.is_active.is_(True),
            TalentPoolEntry.expires_at > datetime.now(UTC),
        )

    rows = (await session.execute(stmt)).all()
    items = [
        _pool_search_item(entry, candidate, cv_id, source_title, None)
        for entry, candidate, cv_id, source_title in rows
    ]
    logger.info(
        "talent_pool.search.ok",
        extra={
            "result_count": len(items),
            "limit": limit,
            "active_only": active_only,
            "mode": "text",
        },
    )
    return TalentPoolSearchResponse(query=query, items=items, total=len(items))
