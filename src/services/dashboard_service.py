"""Dashboard summary service.

A single read-only aggregate over the whole workspace for the HR home
screen. Pure async functions over an ``AsyncSession``; the route layer owns
response shaping (there is no write, so no ``commit()``).

Each breakdown is one ``GROUP BY`` round-trip; the returned map is then
back-filled from the relevant enum so every member is present with a ``0``
default (a stage no application has reached still appears, as ``0``). Totals
are summed from the breakdown rather than issued as a separate ``COUNT(*)``,
keeping the whole summary to four small aggregate queries.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from src.db.models.application import Application
from src.db.models.candidate import Candidate
from src.db.models.interview_booking import InterviewBooking
from src.db.models.job import Job
from src.db.models.talent_pool_entry import TalentPoolEntry
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.dashboard import (
    SHORTLISTED_STAGES,
    STAGE_TO_BUCKET,
    JobHealth,
    PipelineBucket,
)
from src.enums.interviews import InterviewBookingState
from src.enums.jobs import JobStatus
from src.enums.thresholds import (
    INTERVIEW_PENDING_FEEDBACK_DAYS,
    JOB_AT_RISK_WORKING_DAYS,
    JOB_UNHEALTHY_WORKING_DAYS,
)
from src.schemas.dashboard import (
    ApplicationsSummary,
    CandidatePipelineResponse,
    CandidatesSummary,
    DashboardSummaryResponse,
    JobPerformanceResponse,
    JobPerformanceRow,
    JobsSummary,
    PendingFeedbackItem,
    PendingFeedbackResponse,
    TalentPoolSummary,
    UpcomingInterview,
    UpcomingInterviewsResponse,
)


async def _counts_by(
    db: AsyncSession,
    column: InstrumentedAttribute[StrEnum],
    enum_cls: type[StrEnum],
) -> dict[str, int]:
    """Return ``{enum_value: count}`` for ``column``, every member present.

    Members with no rows are filled in as ``0`` so the API contract is the
    full enum value set regardless of the data.
    """
    result = await db.execute(sa.select(column, sa.func.count()).group_by(column))
    observed: dict[StrEnum, int] = {value: count for value, count in result.all()}
    return {member.value: int(observed.get(member, 0)) for member in enum_cls}


async def get_dashboard_summary(db: AsyncSession) -> DashboardSummaryResponse:
    """Aggregate workspace-wide counts for the HR dashboard."""
    jobs_by_status = await _counts_by(db, Job.status, JobStatus)
    apps_by_stage = await _counts_by(db, Application.stage, ApplicationStage)
    apps_by_status = await _counts_by(db, Application.status, ApplicationStatus)

    candidate_total = await db.scalar(sa.select(sa.func.count()).select_from(Candidate))
    talent_pool_active = await db.scalar(
        sa.select(sa.func.count())
        .select_from(TalentPoolEntry)
        .where(TalentPoolEntry.is_active.is_(True))
    )

    return DashboardSummaryResponse(
        jobs=JobsSummary(total=sum(jobs_by_status.values()), by_status=jobs_by_status),
        applications=ApplicationsSummary(
            total=sum(apps_by_status.values()),
            by_stage=apps_by_stage,
            by_status=apps_by_status,
        ),
        candidates=CandidatesSummary(total=candidate_total or 0),
        talent_pool=TalentPoolSummary(active=talent_pool_active or 0),
    )


# --- Performance table ------------------------------------------------------


def _working_days_between(start: datetime, end: datetime) -> int:
    """Count working days (Mon-Fri) in the half-open range ``[start, end)``.

    Weekends (Sat/Sun) are excluded. Mirrors the calendar ``(end - start).days``
    span but drops weekend days, so a job opened on a Friday is only "1 working
    day old" once the following Monday passes. Returns 0 when ``end <= start``.

    Note: this uses a fixed Mon-Fri week. If the deployment's working week
    differs (e.g. a Fri-Sat Gulf weekend), change the weekday check here.
    """
    if end <= start:
        return 0
    start_date = start.date()
    total_days = (end.date() - start_date).days
    full_weeks, remainder = divmod(total_days, 7)
    working = full_weeks * 5
    start_weekday = start_date.weekday()  # Mon=0 ... Sun=6
    for offset in range(remainder):
        if (start_weekday + offset) % 7 < 5:  # Mon-Fri
            working += 1
    return working


def _job_health(*, status: JobStatus, working_days_open: int) -> JobHealth:
    """Age-based health verdict for an open job (unhealthy → at-risk → healthy).

    Only OPEN jobs degrade; anything else (closed / inactive / archived) is
    ``HEALTHY`` regardless of age. Thresholds are working days (see
    :data:`JobHealth` and ``thresholds.JOB_*_WORKING_DAYS``).
    """
    if status != JobStatus.OPEN:
        return JobHealth.HEALTHY
    if working_days_open > JOB_UNHEALTHY_WORKING_DAYS:
        return JobHealth.UNHEALTHY
    if working_days_open >= JOB_AT_RISK_WORKING_DAYS:
        return JobHealth.AT_RISK
    return JobHealth.HEALTHY


async def get_job_performance(db: AsyncSession) -> JobPerformanceResponse:
    """Per-job performance rows for the dashboard Performance table.

    Includes every non-draft job (open + closed); a draft isn't live so has no
    funnel to report. ``candidates`` / ``shortlisted`` count non-archived
    applications (a LEFT JOIN keeps jobs with zero applications present).
    Sorted worst-health first (unhealthy → at-risk → healthy), then by
    ``days_open`` descending, so the rows HR must act on float to the top.
    """
    non_archived = Application.status != ApplicationStatus.ARCHIVED
    is_shortlisted = Application.stage.in_(list(SHORTLISTED_STAGES))

    stmt = (
        sa.select(
            Job.id,
            Job.title,
            Job.status,
            Job.created_at,
            Job.closed_at,
            sa.func.count(Application.id).filter(non_archived).label("candidates"),
            sa.func.count(Application.id)
            .filter(sa.and_(non_archived, is_shortlisted))
            .label("shortlisted"),
        )
        .select_from(Job)
        .outerjoin(Application, Application.job_id == Job.id)
        .where(Job.status != JobStatus.DRAFT)
        .group_by(Job.id, Job.title, Job.status, Job.created_at, Job.closed_at)
    )
    result = await db.execute(stmt)

    now = datetime.now(UTC)
    rows: list[JobPerformanceRow] = []
    for job_id, title, status, created_at, closed_at, candidates, shortlisted in result.all():
        end = closed_at or now
        # ``days_open`` (the table column) stays calendar days; the health
        # verdict below is driven by working days only.
        days_open = (end - created_at).days
        rows.append(
            JobPerformanceRow(
                job_id=job_id,
                title=title,
                status=status,
                candidates=int(candidates),
                shortlisted=int(shortlisted),
                days_open=days_open,
                health=_job_health(
                    status=status,
                    working_days_open=_working_days_between(created_at, end),
                ),
            )
        )

    # Worst health floats to the top (the rows HR must act on), then oldest first.
    health_rank = {JobHealth.UNHEALTHY: 0, JobHealth.AT_RISK: 1, JobHealth.HEALTHY: 2}
    rows.sort(key=lambda r: (health_rank[r.health], -r.days_open))
    return JobPerformanceResponse(rows=rows)


# --- Candidate pipeline -----------------------------------------------------


async def get_candidate_pipeline(
    db: AsyncSession, *, job_id: uuid.UUID | None = None
) -> CandidatePipelineResponse:
    """Candidate Pipeline funnel, optionally scoped to one job.

    ``by_bucket`` collapses *active* applications' stages into the four FE
    buckets (zero-filled); ``by_stage`` keeps the same counts at raw
    five-stage granularity (zero-filled) for the kanban-aligned FE funnel. The
    Applied→Offer conversion counts every non-archived application
    (``applied``) against the accepted ones (``offers``), regardless of stage.
    """
    by_bucket = {bucket.value: 0 for bucket in PipelineBucket}
    by_stage = {stage.value: 0 for stage in ApplicationStage}

    stage_stmt = (
        sa.select(Application.stage, sa.func.count())
        .where(Application.status == ApplicationStatus.ACTIVE)
        .group_by(Application.stage)
    )
    if job_id is not None:
        stage_stmt = stage_stmt.where(Application.job_id == job_id)
    for stage, count in (await db.execute(stage_stmt)).all():
        app_stage = ApplicationStage(stage)
        by_stage[app_stage.value] += int(count)
        bucket = STAGE_TO_BUCKET[app_stage]
        by_bucket[bucket.value] += int(count)

    conv_stmt = sa.select(
        sa.func.count().filter(Application.status != ApplicationStatus.ARCHIVED).label("applied"),
        sa.func.count().filter(Application.status == ApplicationStatus.ACCEPTED).label("offers"),
    ).select_from(Application)
    if job_id is not None:
        conv_stmt = conv_stmt.where(Application.job_id == job_id)
    applied, offers = (await db.execute(conv_stmt)).one()
    applied, offers = int(applied), int(offers)
    conversion_rate = round(offers / applied * 100, 1) if applied else 0.0

    return CandidatePipelineResponse(
        job_id=job_id,
        by_bucket=by_bucket,
        by_stage=by_stage,
        applied=applied,
        offers=offers,
        conversion_rate=conversion_rate,
    )


# --- Upcoming interviews ----------------------------------------------------


async def get_upcoming_interviews(db: AsyncSession, *, limit: int) -> UpcomingInterviewsResponse:
    """Nearest ``limit`` booked, future interviews (soonest first).

    ``total`` is the full count of future booked interviews so the FE's
    "show all" knows whether more exist beyond the returned page.
    """
    now = datetime.now(UTC)
    where = (
        InterviewBooking.state == InterviewBookingState.BOOKED,
        InterviewBooking.scheduled_start_at >= now,
        Application.status != ApplicationStatus.ARCHIVED,
    )

    total = await db.scalar(
        sa.select(sa.func.count())
        .select_from(InterviewBooking)
        .join(Application, InterviewBooking.application_id == Application.id)
        .where(*where)
    )

    stmt = (
        sa.select(
            Application.id,
            Application.job_id,
            Candidate.full_name,
            Job.title,
            InterviewBooking.scheduled_start_at,
            InterviewBooking.scheduled_end_at,
            InterviewBooking.location_type,
            InterviewBooking.join_url,
            InterviewBooking.location_text,
            InterviewBooking.invitee_timezone,
        )
        .select_from(InterviewBooking)
        .join(Application, InterviewBooking.application_id == Application.id)
        .join(Job, Application.job_id == Job.id)
        .join(Candidate, Application.candidate_id == Candidate.id)
        .where(*where)
        .order_by(InterviewBooking.scheduled_start_at.asc())
        .limit(limit)
    )
    result = await db.execute(stmt)

    interviews = [
        UpcomingInterview(
            application_id=row.id,
            job_id=row.job_id,
            candidate_name=row.full_name,
            job_title=row.title,
            scheduled_start_at=row.scheduled_start_at,
            scheduled_end_at=row.scheduled_end_at,
            location_type=row.location_type,
            join_url=row.join_url,
            location_text=row.location_text,
            invitee_timezone=row.invitee_timezone,
        )
        for row in result.all()
    ]
    return UpcomingInterviewsResponse(interviews=interviews, total=int(total or 0))


# --- Pending feedback -------------------------------------------------------


async def get_pending_feedback(db: AsyncSession) -> PendingFeedbackResponse:
    """Active applications stalled at the ``interview`` stage past the SLA.

    An application that entered ``interview`` more than
    ``INTERVIEW_PENDING_FEEDBACK_DAYS`` ago and hasn't advanced is
    awaiting a post-interview decision. Oldest first.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=INTERVIEW_PENDING_FEEDBACK_DAYS)

    stmt = (
        sa.select(
            Application.id,
            Application.job_id,
            Application.stage,
            Application.stage_updated_at,
            Candidate.full_name,
            Job.title,
        )
        .select_from(Application)
        .join(Job, Application.job_id == Job.id)
        .join(Candidate, Application.candidate_id == Candidate.id)
        .where(
            Application.stage == ApplicationStage.INTERVIEW,
            Application.status == ApplicationStatus.ACTIVE,
            Application.stage_updated_at <= cutoff,
        )
        .order_by(Application.stage_updated_at.asc())
    )
    result = await db.execute(stmt)

    items = [
        PendingFeedbackItem(
            application_id=row.id,
            job_id=row.job_id,
            candidate_name=row.full_name,
            job_title=row.title,
            stage=ApplicationStage(row.stage),
            stage_updated_at=row.stage_updated_at,
            days_waiting=(now - row.stage_updated_at).days,
        )
        for row in result.all()
    ]
    return PendingFeedbackResponse(items=items)
