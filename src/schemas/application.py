"""Pydantic request / response shapes for the Applications API.

These are the over-the-wire contracts for ``GET /jobs/{job_id}/applications``,
``GET /applications/{id}``, and ``GET /applications/{id}/audit-log``.

The detail response nests the candidate snapshot, the current CV reference,
and the chronological score history. ``whatsapp_sessions`` / ``interview_slots``
arrive in Phase 5 / 6 — their absence here is intentional, not a placeholder
for empty arrays.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.interviews import InterviewBookingState
from src.enums.score_types import ScoreType
from src.enums.thresholds import (
    INTERVIEW_COMMENT_MAX_LENGTH,
    INTERVIEW_SCORE_MAX,
    INTERVIEW_SCORE_MIN,
)
from src.utils.percentage import percentify_breakdown, to_percent

# Scores and weights are stored numerically in [0, 100] but surfaced to the
# API as percentage strings (e.g. ``"82%"``). These BeforeValidators format
# them on the way out; they are idempotent so an already-formatted string
# (direct construction or a cache round-trip) passes through unchanged.
PercentStr = Annotated[str, BeforeValidator(to_percent)]
PercentStrOptional = Annotated[str | None, BeforeValidator(to_percent)]
PercentBreakdown = Annotated[dict[str, Any], BeforeValidator(percentify_breakdown)]

# Optional free-text justification HR can attach to a transition. Lands
# verbatim in ``audit_logs.after_state['reason']`` for the audit trail.
TRANSITION_REASON_MAX_LENGTH: Final[int] = 500

# Listing pagination caps — kept in sync with job_service so HR list pages
# can use the same UI defaults.
DEFAULT_PAGE_SIZE: Final[int] = 20
MAX_PAGE_SIZE: Final[int] = 100


class ApplicationListOrder(StrEnum):
    """``order=`` query-param values for the list endpoint.

    ``-`` prefix = descending. Default is ``-created_at`` so HR sees the
    most recent submissions first.
    """

    CREATED_AT_DESC = "-created_at"
    CREATED_AT_ASC = "created_at"
    SIMILARITY_DESC = "-similarity_score"
    SIMILARITY_ASC = "similarity_score"
    HARD_FILTER_DESC = "-hard_filter_score"
    HARD_FILTER_ASC = "hard_filter_score"
    STAGE_UPDATED_DESC = "-stage_updated_at"


class CandidateNested(BaseModel):
    """Snapshot of the candidate inside an application detail.

    Surfaces the explainability picture HR needs to understand *why* each
    score landed where it did:

    * ``authenticity_score`` / ``authenticity_band`` — the aggregate verdict.
    * ``authenticity_computed_at`` — when the authenticity run last landed.
    * ``parsed_profile`` — the structured CV (skills, work_history,
      education, languages, total_experience_years) the relevancy and
      CV scores were computed from. ``{}`` until the parse step runs.

    The per-signal authenticity *breakdown* (the "why" behind the score)
    is surfaced in ``ApplicationDetailResponse.scores`` as the
    ``authenticity`` entry — alongside the similarity and hard-filter
    breakdowns — so every score's reasoning lives in one list. Authenticity
    is a property of the candidate's CV, not of any single application,
    which is why the headline score/band still live here.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str | None
    phone_e164: str | None
    full_name: str
    authenticity_score: PercentStrOptional
    authenticity_band: str | None
    authenticity_computed_at: datetime | None
    parsed_profile: dict[str, Any]


class CvDocumentNested(BaseModel):
    """Snapshot of the candidate's current CV referenced by the application.

    ``blob_url`` is a short-lived read-only **SAS URL** (minted per request,
    ~15-min lifetime) so HR can view/download the original PDF directly — the
    storage account blocks public access, so the bare blob URL would 403.
    Treat it as ephemeral: re-fetch the detail endpoint for a fresh link
    rather than persisting it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    blob_url: str
    blob_sha256: str
    language: str | None
    uploaded_at: datetime


class InterviewBookingNested(BaseModel):
    """The interview booking nested on the application detail (Step 6.4).

    Present once HR has moved the application into the ``interview`` stage and
    the invite has been attempted; ``None`` before that (or when interview
    scheduling is disabled). ``scheduling_url`` is the candidate's personal
    single-use Calendly link — safe to show authed HR (e.g. to resend).
    ``last_error`` is a short PII-free reason code (``email_unavailable`` /
    ``unbooked_timeout``) so a stuck or lapsed invite is visible.

    Once the candidate books (``state == booked``) the slot + meeting fields are
    populated from the Calendly webhook: ``scheduled_start_at`` / ``_end_at``,
    the ``location_type`` + ``join_url`` (virtual) or ``location_text``
    (physical/phone), and ``invitee_email`` / ``invitee_timezone``. A reschedule
    keeps ``state == booked`` and fills ``previous_start_at`` / ``rescheduled_at``
    / ``reschedule_count``. On cancel, ``cancel_reason`` / ``canceled_by`` say
    why and who. Internal fields (the tracking token, Calendly resource URIs)
    are intentionally omitted.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    state: InterviewBookingState
    scheduling_url: str | None
    scheduled_start_at: datetime | None
    scheduled_end_at: datetime | None
    # Meeting medium + how to join (set on booking).
    location_type: str | None
    join_url: str | None
    location_text: str | None
    # Who booked (from Calendly) + their timezone for localizing the slot.
    invitee_email: str | None
    invitee_timezone: str | None
    # Reschedule history (slot stays current; prior time kept here).
    previous_start_at: datetime | None
    rescheduled_at: datetime | None
    reschedule_count: int
    # Cancellation detail.
    canceled_by: str | None
    cancel_reason: str | None
    reminder_sent_at: datetime | None
    canceled_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class ApplicationScoreResponse(BaseModel):
    """One score entry in the detail response.

    Usually a row from ``application_scores`` (the append-only audit table)
    — similarity and hard_filter. The ``authenticity`` entry is the one
    exception: authenticity is stored denormalised on the candidate (it's a
    CV-level property, not a per-application row), so it is synthesized into
    this list to keep every score's breakdown in one place. For that entry
    ``id`` is ``None`` — there is no backing ``application_scores`` row.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | None
    score_type: ScoreType
    value: PercentStr
    breakdown: PercentBreakdown
    prompt_version: str
    model_used: str
    computed_at: datetime


class ApplicationListItemResponse(BaseModel):
    """Lightweight row in the list endpoint.

    Excludes the consent context, the pipeline-status JSONB blob, and the
    nested CV — the full picture is one click away via the detail
    endpoint. The denormalised score columns are exposed so HR can sort
    / triage without a second round-trip.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_full_name: str
    candidate_email: str | None
    job_id: uuid.UUID
    stage: ApplicationStage
    status: ApplicationStatus
    similarity_score: PercentStrOptional
    hard_filter_score: PercentStrOptional
    # HR's manual interview mark (set only at the ``interview`` stage), surfaced
    # here so the board's Interviewed column can sort by it. Defaults to None so
    # in-flight entries from the 15s list cache (written before this field
    # existed) still decode cleanly right after a deploy.
    interview_score: PercentStrOptional = None
    stage_updated_at: datetime
    created_at: datetime


class ApplicationListResponse(BaseModel):
    """``GET /jobs/{job_id}/applications`` envelope."""

    items: list[ApplicationListItemResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


class ApplicationDetailResponse(BaseModel):
    """``GET /applications/{id}`` body — full nested application object.

    Per the Step 4.1 architecture-doc acceptance ("Detail returns full
    nested object"), this includes:

    * the application row itself,
    * the candidate snapshot,
    * the current CV reference,
    * the chronological ``application_scores`` history,
    * the interview booking, once the application reaches that stage.

    ``whatsapp_sessions`` is intentionally absent until its table is wired
    into this endpoint — its key appears when it does, so clients don't see
    lying empties. ``interview`` is ``None`` until HR moves the application
    into the ``interview`` stage (or when scheduling is disabled).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    candidate: CandidateNested
    cv_document: CvDocumentNested

    stage: ApplicationStage
    status: ApplicationStatus

    similarity_score: PercentStrOptional
    hard_filter_score: PercentStrOptional

    # HR-entered post-interview evaluation. NULL until HR records feedback at
    # the ``interview`` stage. ``interview_score`` is surfaced as a percentage
    # string ("82%") like the automated scores; the comment is free text.
    interview_score: PercentStrOptional
    interview_comment: str | None
    interview_scored_at: datetime | None

    # Human-readable rejection summary derived at serialisation time from
    # the latest similarity score's breakdown. ``None`` when the
    # application is not rejected, or when the latest similarity score
    # row pre-dates the breakdown rollout (legacy ``breakdown == {}``).
    rejection_reason: str | None

    pipeline_status: dict[str, Any]
    consent_context: dict[str, Any]
    consented_at: datetime

    scores: list[ApplicationScoreResponse]

    interview: InterviewBookingNested | None

    stage_updated_at: datetime
    created_at: datetime
    updated_at: datetime


class AuditLogEntryResponse(BaseModel):
    """One row from ``audit_logs`` filtered to a single application."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    entity_type: str
    entity_id: uuid.UUID
    action: str
    before_state: dict[str, Any] | None
    after_state: dict[str, Any]
    ip_address: str | None
    created_at: datetime


class AuditLogResponse(BaseModel):
    """``GET /applications/{id}/audit-log`` envelope.

    Entries are returned newest-first to match the index order. ``total``
    lets a UI page back through history; first cut returns everything in
    one shot since per-application audit volumes are small.
    """

    items: list[AuditLogEntryResponse]
    total: int = Field(ge=0)


# --- Step 4.2 transition requests -------------------------------------------


class ApplicationStageUpdateRequest(BaseModel):
    """``PATCH /applications/{id}/stage`` body.

    ``reason`` is optional HR-supplied justification surfaced in the
    audit log. ``extra="forbid"`` keeps the API surface tight — adding
    fields requires a deliberate schema change, not a silent acceptance.
    """

    model_config = ConfigDict(extra="forbid")

    stage: ApplicationStage
    reason: str | None = Field(default=None, max_length=TRANSITION_REASON_MAX_LENGTH)


class ApplicationStatusUpdateRequest(BaseModel):
    """``PATCH /applications/{id}/status`` body."""

    model_config = ConfigDict(extra="forbid")

    status: ApplicationStatus
    reason: str | None = Field(default=None, max_length=TRANSITION_REASON_MAX_LENGTH)


# --- Candidate contact edit -------------------------------------------------

# Mirror the column caps on the Candidate model so an over-long value is
# rejected at the schema boundary with a clean 422 rather than a DB error.
CANDIDATE_FULL_NAME_MAX_LENGTH: Final[int] = 255
CANDIDATE_EMAIL_MAX_LENGTH: Final[int] = 254
CANDIDATE_PHONE_MAX_LENGTH: Final[int] = 40


class CandidateContactUpdateRequest(BaseModel):
    """``PATCH /applications/{id}/candidate`` body.

    Edits the underlying candidate's contact details. ``full_name`` is always
    required (the column is NOT NULL); ``email`` and ``phone`` are individually
    optional but the service enforces that **at least one** is present after the
    edit (a candidate must stay reachable). ``phone`` is accepted as the user
    typed it and normalised to E.164 server-side; ``null`` (or omitted) clears
    the field. The edit is only accepted while the application is in the
    ``vector_screen`` / ``hard_filter`` stage — see
    :class:`src.exceptions.CandidateContactLockedError`.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=CANDIDATE_FULL_NAME_MAX_LENGTH)
    email: str | None = Field(default=None, max_length=CANDIDATE_EMAIL_MAX_LENGTH)
    phone: str | None = Field(default=None, max_length=CANDIDATE_PHONE_MAX_LENGTH)


# --- Interview feedback (HR-entered) ----------------------------------------


class InterviewFeedbackUpdateRequest(BaseModel):
    """``PATCH /applications/{id}/interview-feedback`` body.

    HR's post-interview evaluation: a ``score`` in [0, 100] and an optional
    free-text ``comment``. Accepted only while the application is at the
    ``interview`` stage — the service enforces that and raises
    :class:`src.exceptions.InterviewFeedbackStageError` (422) otherwise.
    Re-submitting overwrites the prior mark / comment.
    """

    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=INTERVIEW_SCORE_MIN, le=INTERVIEW_SCORE_MAX)
    comment: str | None = Field(default=None, max_length=INTERVIEW_COMMENT_MAX_LENGTH)


# --- Step 4.4 rescore -------------------------------------------------------


class ApplicationRescoreType(StrEnum):
    """``type=`` query-param values for ``POST /applications/{id}/rescore``.

    Mirrors the architecture-doc Step 4.4 spec (``similarity`` /
    ``hard_filter``). Validated by the route so callers can't enqueue a
    rescore for a score family that doesn't exist yet.
    """

    SIMILARITY = "similarity"
    HARD_FILTER = "hard_filter"


class ApplicationRescoreResponse(BaseModel):
    """``POST /applications/{id}/rescore`` 202 body."""

    application_id: uuid.UUID
    type: ApplicationRescoreType
    enqueued: bool = True
