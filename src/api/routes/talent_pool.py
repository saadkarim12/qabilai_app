"""Talent-pool routes (add / upload / search / list / source).

All endpoints require HR auth. The pool is a holding area of good candidates:

* ``POST /entries`` — add an existing candidate to the pool.
* ``POST /upload`` — upload a CV directly into the pool (no job); identity is
  parsed from the CV and the candidate becomes semantically searchable.
* ``GET /search`` — semantic search over pooled candidates' CVs.
* ``GET ""`` — list pool entries.
* ``GET /candidates/{id}/history`` — a candidate's entire cross-job journey
  (one scored stint per application they ever had, live or archived).
* ``POST /source`` — source a pooled candidate onto a job, creating a fresh
  application (L1, full scoring) flagged so the eventual L2 WhatsApp greeting
  reflects that we already had the candidate.

The remaining Phase-7 surface (scheduled expiry sweep) is deferred.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_hr_user
from src.config import settings
from src.db.models.candidate import Candidate
from src.db.models.user import User
from src.db.session import get_db
from src.integrations.azure_blob import BYTES_PER_MB
from src.schemas.talent_pool import (
    CandidateHistoryResponse,
    TalentPoolAddRequest,
    TalentPoolCandidateNested,
    TalentPoolEntryResponse,
    TalentPoolListResponse,
    TalentPoolSearchResponse,
    TalentPoolSourceRequest,
    TalentPoolSourceResponse,
    TalentPoolUploadResponse,
)
from src.services import bulk_upload_service, talent_pool_service
from src.services.talent_pool_service import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SEARCH_LIMIT,
    MAX_PAGE_SIZE,
    MAX_SEARCH_LIMIT,
)

router = APIRouter(prefix="/talent-pool", tags=["talent-pool"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


@router.post(
    "/entries",
    response_model=TalentPoolEntryResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def add_to_talent_pool_endpoint(
    body: TalentPoolAddRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TalentPoolEntryResponse:
    """Add a candidate to the talent pool (idempotent on the candidate)."""
    entry = await talent_pool_service.add_to_pool(
        db,
        candidate_id=body.candidate_id,
        source_job_id=body.source_job_id,
        actor=current_user,
        ip_address=_client_ip(request),
    )
    await db.commit()

    candidate = await db.get(Candidate, entry.candidate_id)
    return TalentPoolEntryResponse(
        id=entry.id,
        candidate_id=entry.candidate_id,
        source_job_id=entry.source_job_id,
        added_by=entry.added_by,
        added_at=entry.added_at,
        expires_at=entry.expires_at,
        is_active=entry.is_active,
        candidate=TalentPoolCandidateNested.model_validate(candidate),
    )


@router.post(
    "/upload",
    response_model=TalentPoolUploadResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def upload_to_talent_pool_endpoint(
    request: Request,
    current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: Annotated[UploadFile, File()],
) -> TalentPoolUploadResponse:
    """Upload a single CV directly into the talent pool (no job needed).

    Identity is extracted from the CV itself; the candidate is created (or
    matched) and added to the pool. After commit the parse/authenticity/embed
    pipeline runs so the candidate becomes semantically searchable. The
    per-file size cap is enforced while reading so an oversized PDF never lands
    fully in memory.
    """
    max_bytes = settings.cv_max_file_size_mb * BYTES_PER_MB
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        await file.close()
        raise HTTPException(
            status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"CV exceeds the {settings.cv_max_file_size_mb} MB limit.",
        )

    result = await talent_pool_service.upload_to_pool(
        db,
        pdf_bytes=data,
        actor=current_user,
        ip_address=_client_ip(request),
    )
    await db.commit()

    # Dispatch the CV pipeline only after commit (so the worker can't read a
    # not-yet-visible row) and only when the CV still needs embedding.
    enqueued = False
    if result.needs_pipeline:
        talent_pool_service._enqueue_pool_cv_pipeline(result.cv_document_id, result.candidate.id)
        enqueued = True

    return TalentPoolUploadResponse(
        entry=TalentPoolEntryResponse(
            id=result.entry.id,
            candidate_id=result.entry.candidate_id,
            source_job_id=result.entry.source_job_id,
            added_by=result.entry.added_by,
            added_at=result.entry.added_at,
            expires_at=result.entry.expires_at,
            is_active=result.entry.is_active,
            candidate=TalentPoolCandidateNested.model_validate(result.candidate),
        ),
        candidate_created=result.candidate_created,
        cv_created=result.cv_created,
        enqueued=enqueued,
    )


@router.get("/search", response_model=TalentPoolSearchResponse)
async def search_talent_pool_endpoint(
    _current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str | None, Query(min_length=1, max_length=1000)] = None,
    job_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_SEARCH_LIMIT)] = DEFAULT_SEARCH_LIMIT,
    active_only: Annotated[bool, Query()] = True,
) -> TalentPoolSearchResponse:
    """Semantic search over the talent pool.

    Ranks pooled candidates by cosine similarity of their current CV against a
    target vector — either the free-text query ``q`` or, when ``job_id`` is
    given, that job's stored JD embedding ("candidates relevant to this job").
    At least one of ``q`` / ``job_id`` is required (else 422); ``job_id`` wins
    when both are supplied. Candidates whose CV isn't embedded yet are excluded.
    """
    return await talent_pool_service.search_pool(
        db,
        query=q,
        job_id=job_id,
        limit=limit,
        active_only=active_only,
    )


@router.get("", response_model=TalentPoolListResponse)
async def list_talent_pool_endpoint(
    _current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    active_only: Annotated[bool, Query()] = True,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> TalentPoolListResponse:
    """List talent-pool entries (newest first) so HR can pick whom to source."""
    return await talent_pool_service.list_pool(
        db,
        active_only=active_only,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/candidates/{candidate_id}/history",
    response_model=CandidateHistoryResponse,
)
async def candidate_history_endpoint(
    candidate_id: uuid.UUID,
    _current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CandidateHistoryResponse:
    """Return a candidate's full cross-job history for the talent-pool view.

    One stint per application the candidate ever had — live, rejected,
    accepted, or archived (moved back to the pool) — each scored against its
    own job, plus its WhatsApp screening digest. Candidate-level authenticity
    is surfaced once on the candidate snapshot. 404 if the candidate is unknown.
    """
    return await talent_pool_service.get_candidate_history(db, candidate_id=candidate_id)


@router.post(
    "/source",
    response_model=TalentPoolSourceResponse,
    status_code=http_status.HTTP_202_ACCEPTED,
)
async def source_from_talent_pool_endpoint(
    body: TalentPoolSourceRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TalentPoolSourceResponse:
    """Source a pooled candidate onto a job.

    Creates a fresh application at ``vector_screen`` (unless one already
    exists) flagged ``sourced_from_talent_pool``, then — after commit —
    dispatches the CV scoring pipeline so the candidate is scored against this
    job from scratch. The differentiated WhatsApp greeting fires later, when HR
    advances the application to the ``whatsapp`` stage.
    """
    application, already_existed = await talent_pool_service.source_to_job(
        db,
        candidate_id=body.candidate_id,
        job_id=body.job_id,
        actor=current_user,
        ip_address=_client_ip(request),
    )
    await db.commit()

    # Dispatch the pipeline only for a newly-created application, and only
    # after commit (so the worker can't read a not-yet-visible row).
    enqueued = False
    if not already_existed:
        bulk_upload_service._enqueue_cv_pipeline(application.id)
        enqueued = True

    return TalentPoolSourceResponse(
        application_id=application.id,
        candidate_id=body.candidate_id,
        job_id=body.job_id,
        sourced_from_talent_pool=application.sourced_from_talent_pool,
        already_existed=already_existed,
        enqueued=enqueued,
    )
