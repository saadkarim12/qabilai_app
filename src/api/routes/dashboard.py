"""Dashboard route.

A single read-only HR endpoint that returns workspace-wide aggregate counts
for the home screen. Requires HR auth like every other non-public route; the
service layer owns the aggregate queries.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_hr_user
from src.db.models.user import User
from src.db.session import get_db
from src.enums.thresholds import (
    DASHBOARD_UPCOMING_INTERVIEWS_MAX,
    DASHBOARD_UPCOMING_INTERVIEWS_PREVIEW,
)
from src.schemas.dashboard import (
    CandidatePipelineResponse,
    DashboardSummaryResponse,
    JobPerformanceResponse,
    PendingFeedbackResponse,
    UpcomingInterviewsResponse,
)
from src.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardSummaryResponse)
async def get_dashboard_endpoint(
    _current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DashboardSummaryResponse:
    return await dashboard_service.get_dashboard_summary(db)


@router.get("/performance", response_model=JobPerformanceResponse)
async def get_dashboard_performance_endpoint(
    _current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobPerformanceResponse:
    """Per-job performance table (one row per non-draft job, at-risk first)."""
    return await dashboard_service.get_job_performance(db)


@router.get("/pipeline", response_model=CandidatePipelineResponse)
async def get_dashboard_pipeline_endpoint(
    _current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    job_id: Annotated[uuid.UUID | None, Query()] = None,
) -> CandidatePipelineResponse:
    """Candidate Pipeline funnel; omit ``job_id`` for the All-Jobs view."""
    return await dashboard_service.get_candidate_pipeline(db, job_id=job_id)


@router.get("/upcoming-interviews", response_model=UpcomingInterviewsResponse)
async def get_dashboard_upcoming_interviews_endpoint(
    _current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[
        int, Query(ge=1, le=DASHBOARD_UPCOMING_INTERVIEWS_MAX)
    ] = DASHBOARD_UPCOMING_INTERVIEWS_PREVIEW,
) -> UpcomingInterviewsResponse:
    """Nearest upcoming interviews. Defaults to the 3-card preview; the
    "show all" view passes a larger ``limit``."""
    return await dashboard_service.get_upcoming_interviews(db, limit=limit)


@router.get("/pending-feedback", response_model=PendingFeedbackResponse)
async def get_dashboard_pending_feedback_endpoint(
    _current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PendingFeedbackResponse:
    """Applications stalled at the interview stage past the feedback SLA."""
    return await dashboard_service.get_pending_feedback(db)
