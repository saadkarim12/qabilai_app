"""Application listing / detail / audit-log routes.

All endpoints require HR auth. The route layer owns request shaping and
response serialization; the service raises domain exceptions
(``JobNotFoundError``, ``ApplicationNotFoundError``) the global handler
maps to 404.

The list endpoint sits under ``/jobs/{job_id}/applications`` because the
job is the dominant grouping in the HR UI; the detail and audit-log
endpoints sit at the top-level ``/applications`` namespace because they
key on an application id, not a job.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_hr_user
from src.config import settings
from src.db.models.candidate import Candidate
from src.db.models.user import User
from src.db.session import get_db
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.schemas.application import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ApplicationDetailResponse,
    ApplicationListOrder,
    ApplicationListResponse,
    ApplicationRescoreResponse,
    ApplicationRescoreType,
    ApplicationStageUpdateRequest,
    ApplicationStatusUpdateRequest,
    AuditLogResponse,
    CandidateContactUpdateRequest,
    InterviewFeedbackUpdateRequest,
)
from src.schemas.bulk_upload import (
    BULK_UPLOAD_MAX_FILES,
    BULK_UPLOAD_MIN_FILES,
    BulkUploadRejectionReason,
    BulkUploadResponse,
)
from src.schemas.talent_pool import (
    MoveApplicationToPoolRequest,
    TalentPoolCandidateNested,
    TalentPoolEntryResponse,
)
from src.schemas.whatsapp import WhatsAppConversationResponse
from src.services import (
    application_service,
    bulk_upload_service,
    talent_pool_service,
    whatsapp_conversation_service,
)
from src.services.bulk_upload_service import FileSubmission
from src.workers.tasks.cv_pipeline import score_similarity_task
from src.workers.tasks.interviews import send_interview_invite_task
from src.workers.tasks.scoring import score_hard_filters_task
from src.workers.tasks.whatsapp import send_whatsapp_invite_task

# Per-file size cap mirrors the public-apply route's enforcement in
# :func:`src.integrations.azure_blob.upload_pdf`. Bytes are checked *as
# they're read* so an oversized file never lands fully in memory.
BYTES_PER_MB: Final[int] = 1024 * 1024

# Two routers because the list endpoint lives under ``/jobs/{job_id}/...``
# while detail + audit-log live under ``/applications/...``. The same
# tag groups them in the OpenAPI docs.
jobs_applications_router = APIRouter(prefix="/jobs", tags=["applications"])
applications_router = APIRouter(prefix="/applications", tags=["applications"])


@jobs_applications_router.get(
    "/{job_id}/applications",
    response_model=ApplicationListResponse,
)
async def list_applications_endpoint(
    job_id: uuid.UUID,
    _current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    stage: Annotated[ApplicationStage | None, Query()] = None,
    status: Annotated[ApplicationStatus | None, Query()] = None,
    order: Annotated[ApplicationListOrder, Query()] = ApplicationListOrder.CREATED_AT_DESC,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> ApplicationListResponse:
    return await application_service.list_applications_cached(
        db,
        job_id=job_id,
        stage=stage,
        status=status,
        order=order,
        page=page,
        page_size=page_size,
    )


@applications_router.get(
    "/{application_id}",
    response_model=ApplicationDetailResponse,
)
async def get_application_endpoint(
    application_id: uuid.UUID,
    _current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApplicationDetailResponse:
    return await application_service.get_application_detail(db, application_id)


@applications_router.get(
    "/{application_id}/audit-log",
    response_model=AuditLogResponse,
)
async def get_application_audit_log_endpoint(
    application_id: uuid.UUID,
    _current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AuditLogResponse:
    return await application_service.get_application_audit_log(db, application_id)


@applications_router.get(
    "/{application_id}/whatsapp",
    response_model=WhatsAppConversationResponse,
)
async def get_application_whatsapp_conversation_endpoint(
    application_id: uuid.UUID,
    _current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WhatsAppConversationResponse:
    """Return the candidate's WhatsApp screening transcript for HR.

    404 (``whatsapp_conversation_not_found``) until the application has been
    moved into the ``whatsapp`` stage and the invite has opened a conversation.
    """
    return await whatsapp_conversation_service.get_conversation_detail(db, application_id)


# --- Step 4.2: PATCH transitions --------------------------------------------


def _client_ip(request: Request) -> str | None:
    """Best-effort IP extraction for the audit-log row.

    ``request.client`` may be ``None`` under some ASGI test transports;
    we treat that as "unknown" rather than error. Behind a real proxy
    the IP would come from ``X-Forwarded-For`` — a TODO for the proxy
    integration step.
    """
    return request.client.host if request.client is not None else None


@applications_router.patch(
    "/{application_id}/stage",
    response_model=ApplicationDetailResponse,
)
async def patch_application_stage_endpoint(
    application_id: uuid.UUID,
    body: ApplicationStageUpdateRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApplicationDetailResponse:
    """Move an application forward through the stage matrix.

    Validation matrix lives in :mod:`src.enums.stages`; the service
    raises :class:`IllegalStageTransitionError` (422) on a disallowed
    edge. On a successful transition to ``HARD_FILTER`` the route
    dispatches the Step 4.3 scoring task *after* the transaction
    commits, so the worker can't pick up a row that isn't visible yet.
    """
    _, entered_stage = await application_service.update_application_stage(
        db,
        application_id=application_id,
        new_stage=body.stage,
        actor=current_user,
        reason=body.reason,
        ip_address=_client_ip(request),
    )
    await db.commit()

    # Dispatch the stage's follow-up task only after the commit, so the worker
    # can't read a row that isn't visible yet.
    if entered_stage is ApplicationStage.HARD_FILTER:
        score_hard_filters_task.apply_async(args=[str(application_id)])
    elif entered_stage is ApplicationStage.WHATSAPP:
        send_whatsapp_invite_task.apply_async(args=[str(application_id)])
    elif entered_stage is ApplicationStage.INTERVIEW and settings.interview_scheduling_enabled:
        # Gated by the kill switch so a deployment without Calendly configured
        # advances to the interview stage without enqueuing a doomed invite.
        send_interview_invite_task.apply_async(args=[str(application_id)])

    return await application_service.get_application_detail(db, application_id)


@applications_router.patch(
    "/{application_id}/status",
    response_model=ApplicationDetailResponse,
)
async def patch_application_status_endpoint(
    application_id: uuid.UUID,
    body: ApplicationStatusUpdateRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApplicationDetailResponse:
    """Flip an application's status (active / rejected / accepted).

    The service guarantees the (old → new) edge is in the allow-list
    and writes the audit-log row in the same transaction. Rejected →
    active is the "HR override" path; stage stays where it was so the
    candidate resumes from the same place.

    Reactivating (→ active) an application that's parked in the
    ``whatsapp`` stage re-opens its screening conversation: after the
    commit we enqueue the invite task, which sends the *welcome-back*
    greeting on the existing conversation (see
    :func:`whatsapp_conversation_service.begin_screening`).
    """
    app = await application_service.update_application_status(
        db,
        application_id=application_id,
        new_status=body.status,
        actor=current_user,
        reason=body.reason,
        ip_address=_client_ip(request),
    )
    await db.commit()

    # Reactivation restart: only a → active move can produce ACTIVE (the
    # matrix forbids active → active), so this fires exactly on the HR
    # override, and only for apps sitting in the WhatsApp stage.
    if body.status is ApplicationStatus.ACTIVE and app.stage is ApplicationStage.WHATSAPP:
        send_whatsapp_invite_task.apply_async(args=[str(application_id)])

    return await application_service.get_application_detail(db, application_id)


# --- Candidate contact edit -------------------------------------------------


@applications_router.patch(
    "/{application_id}/candidate",
    response_model=ApplicationDetailResponse,
)
async def patch_application_candidate_endpoint(
    application_id: uuid.UUID,
    body: CandidateContactUpdateRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApplicationDetailResponse:
    """Edit the candidate's contact details (name / email / phone).

    Permitted only while the application is in the ``vector_screen`` /
    ``hard_filter`` stage; from ``whatsapp`` onward the screening flow owns the
    contact channel and the service raises ``CandidateContactLockedError`` (422).
    The candidate row is shared, so the edit propagates to every application the
    person holds. Returns the refreshed application detail.
    """
    await application_service.update_application_candidate(
        db,
        application_id=application_id,
        full_name=body.full_name,
        email=body.email,
        phone=body.phone,
        actor=current_user,
        ip_address=_client_ip(request),
    )
    await db.commit()
    return await application_service.get_application_detail(db, application_id)


# --- Interview feedback -----------------------------------------------------


@applications_router.patch(
    "/{application_id}/interview-feedback",
    response_model=ApplicationDetailResponse,
)
async def patch_application_interview_feedback_endpoint(
    application_id: uuid.UUID,
    body: InterviewFeedbackUpdateRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApplicationDetailResponse:
    """Record HR's post-interview evaluation (mark + optional comment).

    Accepted only while the application is at the ``interview`` stage; the
    service raises ``InterviewFeedbackStageError`` (422) otherwise. Re-submitting
    overwrites the previous mark / comment. Returns the refreshed detail.
    """
    await application_service.update_interview_feedback(
        db,
        application_id=application_id,
        score=body.score,
        comment=body.comment,
        actor=current_user,
        ip_address=_client_ip(request),
    )
    await db.commit()
    return await application_service.get_application_detail(db, application_id)


# --- Step 4.4: manual rescore -----------------------------------------------


@applications_router.post(
    "/{application_id}/rescore",
    response_model=ApplicationRescoreResponse,
    status_code=http_status.HTTP_202_ACCEPTED,
)
async def post_application_rescore_endpoint(
    application_id: uuid.UUID,
    request: Request,
    current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    type: Annotated[ApplicationRescoreType, Query()],
) -> ApplicationRescoreResponse:
    """HR-triggered manual rescore.

    Writes a ``rescore_requested`` audit-log row, commits, and dispatches
    the matching scoring task with ``force=True`` so the worker:

    * skips the idempotent return on an existing denormalised score,
    * nulls the existing score / breakdown so the detail endpoint shows
      "in progress" until the new run lands,
    * for ``hard_filter``: passes ``use_cache=False`` to the Claude
      wrapper so HR gets a genuinely fresh Claude response (not cached).

    For ``similarity`` the cache bypass is a no-op -- the calculation
    is pure vector math against already-cached embeddings -- so
    ``force=True`` just forces the recompute.

    Returns 202 ``{application_id, type, enqueued: true}``. The new
    score becomes visible on the detail endpoint when the worker
    finishes.
    """
    await application_service.audit_rescore_requested(
        db,
        application_id=application_id,
        rescore_type=type.value,
        actor=current_user,
        ip_address=_client_ip(request),
    )
    await db.commit()

    # Dispatch after commit so the worker can't pick up an audit row
    # that isn't visible yet.
    if type is ApplicationRescoreType.HARD_FILTER:
        score_hard_filters_task.apply_async(args=[str(application_id), True])
    else:
        score_similarity_task.apply_async(args=[str(application_id), True])

    return ApplicationRescoreResponse(application_id=application_id, type=type)


# --- Move back to talent pool -----------------------------------------------


@applications_router.post(
    "/{application_id}/move-to-pool",
    response_model=TalentPoolEntryResponse,
    status_code=http_status.HTTP_200_OK,
)
async def move_application_to_pool_endpoint(
    application_id: uuid.UUID,
    request: Request,
    current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    body: MoveApplicationToPoolRequest | None = None,
) -> TalentPoolEntryResponse:
    """Move a candidate out of this job's pipeline back into the talent pool.

    Soft-archives the application (``status=archived``; its scores + WhatsApp
    transcript are retained as cross-job history) and (re-)adds the candidate
    to the pool. Only this job is affected; the candidate can be sourced back
    onto it for a fresh stint afterwards. An optional ``reason`` is stored
    against this stint and mirrored into the audit log. The body itself is
    optional, so a bare POST (no reason) still works.
    """
    entry = await talent_pool_service.move_application_to_pool(
        db,
        application_id=application_id,
        actor=current_user,
        ip_address=_client_ip(request),
        reason=body.reason if body else None,
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


# --- HR bulk CV upload ------------------------------------------------------


@jobs_applications_router.post(
    "/{job_id}/applications/bulk-upload",
    response_model=BulkUploadResponse,
    status_code=http_status.HTTP_202_ACCEPTED,
)
async def bulk_upload_applications_endpoint(
    job_id: uuid.UUID,
    request: Request,
    current_user: Annotated[User, Depends(get_current_hr_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    files: Annotated[list[UploadFile], File()],
) -> BulkUploadResponse:
    """Accept up to ``BULK_UPLOAD_MAX_FILES`` PDFs from an HR admin in one request.

    Fail-soft: each file is validated individually. Bad files come back
    in ``rejected`` with a reason; good ones land in ``applications``
    (newly created) or ``already_applied`` (candidate already has an
    application on this job). The whole request fails (400) only if the
    file count is out of bounds; everything else surfaces per-file.

    Per-file size cap is enforced *while reading*: we ask for at most
    ``max_bytes + 1`` from each upload's spooled file, so an oversized
    PDF never lands fully in memory.
    """
    if not (BULK_UPLOAD_MIN_FILES <= len(files) <= BULK_UPLOAD_MAX_FILES):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=(
                f"files must be between {BULK_UPLOAD_MIN_FILES} and "
                f"{BULK_UPLOAD_MAX_FILES} (got {len(files)})"
            ),
        )

    max_bytes = settings.cv_max_file_size_mb * BYTES_PER_MB
    submissions: list[FileSubmission] = []
    for upload in files:
        data = await upload.read(max_bytes + 1)
        if len(data) > max_bytes:
            # Drain and drop the rest so the multipart parser doesn't
            # buffer the overflow into memory.
            await upload.close()
            submissions.append(
                FileSubmission(
                    filename=upload.filename or "",
                    pdf_bytes=None,
                    prevalidation_error=BulkUploadRejectionReason.TOO_LARGE,
                )
            )
            continue
        submissions.append(
            FileSubmission(
                filename=upload.filename or "",
                pdf_bytes=data,
                prevalidation_error=None,
            )
        )

    response = await bulk_upload_service.process_batch(
        db,
        job_id=job_id,
        actor=current_user,
        ip=_client_ip(request),
        submissions=submissions,
    )
    await db.commit()

    # Dispatch the CV pipeline only *after* the commit, so the worker
    # never picks up a task for an application row that isn't visible
    # yet (enqueuing mid-transaction races the commit and the dispatcher
    # bails with ``cv_pipeline.dispatch.app_missing``, silently dropping
    # the CV). Only newly-created applications are enqueued.
    for created in response.applications:
        bulk_upload_service._enqueue_cv_pipeline(created.application_id)

    return response
