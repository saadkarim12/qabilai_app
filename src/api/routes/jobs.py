"""Job CRUD routes.

All endpoints require HR auth (see :func:`get_current_hr_user`). The
route layer owns request/response shaping and ``db.commit()``; the
service layer owns business logic and raises domain exceptions.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.job_description_generator import generate_job_description
from src.api.deps import get_current_hr_user
from src.db.models.user import User
from src.db.session import get_db
from src.enums.jobs import JobListOrder, JobStatus
from src.schemas.job import (
    JobCreateRequest,
    JobCreateResponse,
    JobDescriptionGenerateRequest,
    JobDescriptionGenerateResponse,
    JobDetailResponse,
    JobListItemResponse,
    JobListResponse,
    JobStatusUpdateRequest,
    WhatsAppQuestionsResponse,
    WhatsAppQuestionsUpdateRequest,
)
from src.services import job_service
from src.services.job_service import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from src.workers.tasks.job_pipeline import embed_jd_task, generate_questions_task

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "",
    response_model=JobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_job_endpoint(
    body: JobCreateRequest,
    current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobCreateResponse:
    job = await job_service.create_job(db, request=body, created_by=current_user)
    await db.commit()
    return JobCreateResponse(id=job.id)


@router.post("/generate-description", response_model=JobDescriptionGenerateResponse)
async def generate_job_description_endpoint(
    body: JobDescriptionGenerateRequest,
    _current_user: Annotated[User, Depends(get_current_hr_user)],
    regenerate: Annotated[bool, Query()] = False,
) -> JobDescriptionGenerateResponse:
    """Draft a bilingual job description from the Role-Basics fields (AI JD Builder).

    Stateless — no job is created. The wizard calls this on the JD Builder step
    to pre-fill the English + native Gulf-Arabic descriptions, which HR can then
    edit. ``regenerate=true`` bypasses the Claude cache for a fresh draft on a
    repeat click.
    """
    draft = await generate_job_description(spec=body, regenerate=regenerate)
    return JobDescriptionGenerateResponse(
        job_description=draft.job_description_en,
        job_description_ar=draft.job_description_ar,
    )


@router.get("", response_model=JobListResponse)
async def list_jobs_endpoint(
    _current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    order: Annotated[JobListOrder, Query()] = JobListOrder.UPDATED_AT,
) -> JobListResponse:
    items, total, stage_counts = await job_service.list_jobs(
        db,
        status=status_filter,
        search=search,
        page=page,
        page_size=page_size,
        order=order,
    )
    return JobListResponse(
        items=[
            JobListItemResponse.model_validate(j).model_copy(
                update={"applications_by_stage": stage_counts[j.id]}
            )
            for j in items
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job_endpoint(
    job_id: uuid.UUID,
    _current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobDetailResponse:
    return await job_service.get_job_detail_cached(db, job_id)


@router.patch("/{job_id}/status", response_model=JobDetailResponse)
async def update_job_status_endpoint(
    job_id: uuid.UUID,
    body: JobStatusUpdateRequest,
    current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobDetailResponse:
    job, just_opened = await job_service.update_status(
        db, job_id=job_id, new_status=body.status, actor=current_user
    )
    await db.commit()

    # Dispatch the pipeline only AFTER the transaction commits so the worker
    # can't pick up a row that isn't visible yet. The ``ready_for_applications``
    # short-circuit makes closed→open→closed→open cycles cheap.
    if just_opened and not job.ready_for_applications:
        job_id_str = str(job.id)
        embed_jd_task.apply_async(args=[job_id_str])
        generate_questions_task.apply_async(args=[job_id_str])

    return JobDetailResponse.model_validate(job)


@router.get(
    "/{job_id}/whatsapp-questions",
    response_model=WhatsAppQuestionsResponse,
)
async def get_whatsapp_questions_endpoint(
    job_id: uuid.UUID,
    _current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WhatsAppQuestionsResponse:
    questions = await job_service.get_whatsapp_questions(db, job_id)
    return WhatsAppQuestionsResponse(questions=questions)


@router.patch(
    "/{job_id}/whatsapp-questions",
    response_model=WhatsAppQuestionsResponse,
)
async def update_whatsapp_questions_endpoint(
    job_id: uuid.UUID,
    body: WhatsAppQuestionsUpdateRequest,
    current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WhatsAppQuestionsResponse:
    questions = await job_service.replace_whatsapp_questions(
        db, job_id=job_id, request=body, actor=current_user
    )
    await db.commit()
    return WhatsAppQuestionsResponse(questions=questions)
