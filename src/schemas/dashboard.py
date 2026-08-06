"""Dashboard summary response shapes.

A single read-only aggregate view of the whole workspace for the HR home
screen. The system is a single shared workspace (no org/tenancy), so these
counts span every job, application, and candidate — there is nothing to
scope by.

The ``by_status`` / ``by_stage`` maps are keyed by the corresponding enum
*values* (``src/enums/jobs.py`` ``JobStatus``; ``src/enums/applications.py``
``ApplicationStage`` / ``ApplicationStatus``) and are always fully populated
— every member is present with a ``0`` default so the FE never has to guard
a missing key. The enum value set is the API contract.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from src.enums.applications import ApplicationStage
from src.enums.dashboard import JobHealth
from src.enums.jobs import JobStatus


class JobsSummary(BaseModel):
    """Job counts, total and broken down by lifecycle status."""

    total: int
    by_status: dict[str, int]


class ApplicationsSummary(BaseModel):
    """Application counts, total and broken down by pipeline stage and status."""

    total: int
    by_stage: dict[str, int]
    by_status: dict[str, int]


class CandidatesSummary(BaseModel):
    """Candidate counts (one row per person, shared across their applications)."""

    total: int


class TalentPoolSummary(BaseModel):
    """Talent-pool counts. ``active`` excludes deactivated entries."""

    active: int


class DashboardSummaryResponse(BaseModel):
    """Workspace-wide aggregate counts for the HR dashboard."""

    jobs: JobsSummary
    applications: ApplicationsSummary
    candidates: CandidatesSummary
    talent_pool: TalentPoolSummary


# --- Performance table ------------------------------------------------------


class JobPerformanceRow(BaseModel):
    """One row of the dashboard Performance table — a single job's health.

    ``candidates`` / ``shortlisted`` exclude archived (moved-to-pool) stints.
    ``shortlisted`` counts applications that reached the terminal ``done``
    stage. ``days_open`` is ``(closed_at or now) - created_at`` in whole days
    (jobs have no separate ``opened_at``, so creation time is the proxy).
    ``health`` is the :class:`JobHealth` verdict.
    """

    job_id: uuid.UUID
    title: str
    status: JobStatus
    candidates: int
    shortlisted: int
    days_open: int
    health: JobHealth


class JobPerformanceResponse(BaseModel):
    """The Performance table: one row per non-draft job, at-risk first."""

    rows: list[JobPerformanceRow]


# --- Candidate pipeline -----------------------------------------------------


class CandidatePipelineResponse(BaseModel):
    """The Candidate Pipeline funnel, optionally scoped to one job.

    ``job_id`` is ``None`` for the workspace-wide ("All Jobs") view. ``by_bucket``
    counts *active* applications collapsed into the four
    :class:`PipelineBucket` columns, always fully populated (every bucket key
    present, zero-filled). ``by_stage`` is the same active-application counts
    kept at the raw five-:class:`~src.enums.applications.ApplicationStage`
    granularity (every stage key present, zero-filled) so the FE can mirror the
    kanban board's five columns without collapsing. ``applied`` / ``offers``
    drive the Applied→Offer conversion: ``applied`` is every non-archived
    application in scope, ``offers`` those with ``status == accepted``;
    ``conversion_rate`` is ``offers / applied`` as a 0-100 percentage (``0.0``
    when none applied).
    """

    job_id: uuid.UUID | None
    by_bucket: dict[str, int]
    by_stage: dict[str, int]
    applied: int
    offers: int
    conversion_rate: float


# --- Upcoming interviews ----------------------------------------------------


class UpcomingInterview(BaseModel):
    """One booked, future interview slot for the dashboard.

    Sourced from an ``interview_bookings`` row in the ``booked`` state whose
    ``scheduled_start_at`` is in the future. Meeting-location fields are
    whichever Calendly supplied (``join_url`` for virtual, ``location_text``
    for physical/phone); the FE shows whichever is non-null.
    """

    application_id: uuid.UUID
    job_id: uuid.UUID
    candidate_name: str
    job_title: str
    scheduled_start_at: datetime
    scheduled_end_at: datetime | None
    location_type: str | None
    join_url: str | None
    location_text: str | None
    invitee_timezone: str | None


class UpcomingInterviewsResponse(BaseModel):
    """Nearest upcoming interviews. ``total`` is the full count of future
    booked interviews so the FE knows whether "show all" has more than the
    returned page."""

    interviews: list[UpcomingInterview]
    total: int


# --- Pending feedback -------------------------------------------------------


class PendingFeedbackItem(BaseModel):
    """An application stalled at the ``interview`` stage past the feedback SLA.

    ``days_waiting`` is whole days since ``stage_updated_at`` (when the app
    entered the interview stage). Surfaces only active applications still at
    ``stage == interview`` older than ``THRESHOLDS.INTERVIEW_PENDING_FEEDBACK_DAYS``.
    """

    application_id: uuid.UUID
    job_id: uuid.UUID
    candidate_name: str
    job_title: str
    stage: ApplicationStage
    stage_updated_at: datetime
    days_waiting: int


class PendingFeedbackResponse(BaseModel):
    """Applications awaiting a post-interview decision, oldest first."""

    items: list[PendingFeedbackItem]
