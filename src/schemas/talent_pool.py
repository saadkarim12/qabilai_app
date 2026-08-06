"""Pydantic request / response shapes for the Talent Pool API.

Covers the slice needed for WhatsApp sourcing:

* ``POST /talent-pool/entries`` — add a candidate to the pool.
* ``GET /talent-pool`` — list entries (so HR can pick whom to source).
* ``POST /talent-pool/source`` — create a fresh application for a pooled
  candidate on a job (starts at L1, full scoring; only the eventual L2
  greeting differs).
* ``GET /talent-pool/candidates/{id}/history`` — a candidate's entire
  cross-job journey: one scored stint per application they ever had (live or
  archived), so the pool view shows the full picture.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.whatsapp import WhatsAppConversationState
from src.schemas.application import (
    TRANSITION_REASON_MAX_LENGTH,
    ApplicationScoreResponse,
    CandidateNested,
    PercentStrOptional,
)


class TalentPoolAddRequest(BaseModel):
    """Body for adding a candidate to the pool."""

    candidate_id: uuid.UUID
    # Where they were sourced from; omit for a direct/no-job entry.
    source_job_id: uuid.UUID | None = None


class MoveApplicationToPoolRequest(BaseModel):
    """Body for ``POST /applications/{id}/move-to-pool``.

    ``reason`` is an optional HR-supplied justification for *why* the candidate
    is being moved back to the pool. It's stored against this application (the
    archived job stint) and mirrored into the audit log; omit it for no reason.
    """

    reason: str | None = Field(default=None, max_length=TRANSITION_REASON_MAX_LENGTH)


class TalentPoolCandidateNested(BaseModel):
    """Candidate snapshot shown in a pool listing / search hit.

    ``role`` (most-recent work-history title) and ``skills`` are derived from
    the candidate's ``parsed_profile``; ``authenticity_score`` / ``_band`` are
    the candidate-level CV-trust verdict. All are ``None``/empty until the CV
    pipeline has parsed + scored the candidate.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: str | None
    phone_e164: str | None
    role: str | None = None
    skills: list[str] = Field(default_factory=list)
    authenticity_score: float | None = None
    authenticity_band: str | None = None


class TalentPoolEntryResponse(BaseModel):
    """One talent-pool entry, with the candidate snapshot for the list view."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID
    source_job_id: uuid.UUID | None
    # Title of the job the candidate was sourced from; None = direct upload.
    source_job_title: str | None = None
    added_by: uuid.UUID | None
    added_at: datetime
    expires_at: datetime
    is_active: bool
    candidate: TalentPoolCandidateNested


class TalentPoolListResponse(BaseModel):
    """``GET /talent-pool`` body."""

    items: list[TalentPoolEntryResponse]
    total: int


class TalentPoolUploadResponse(BaseModel):
    """``POST /talent-pool/upload`` body — direct CV upload into the pool.

    ``candidate_created`` / ``cv_created`` report whether the upload minted a
    new candidate / CV row or matched (deduped to) an existing one. ``enqueued``
    is True when the CV-processing pipeline (parse + authenticity + embed) was
    dispatched — done only when the CV still needs an embedding for search.
    """

    entry: TalentPoolEntryResponse
    candidate_created: bool
    cv_created: bool
    enqueued: bool = Field(
        description="True if the CV parse/authenticity/embed pipeline was dispatched."
    )


class TalentPoolSourceRequest(BaseModel):
    """Body for sourcing a pooled candidate onto a job."""

    candidate_id: uuid.UUID
    job_id: uuid.UUID


class TalentPoolSourceResponse(BaseModel):
    """``POST /talent-pool/source`` body.

    ``already_existed`` is True when the candidate already had an application
    on that job (the unique ``(candidate, job)`` constraint), in which case
    no new row was created and no pipeline was enqueued.
    """

    application_id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    sourced_from_talent_pool: bool
    already_existed: bool = Field(
        description="True if an application for this candidate+job already existed."
    )
    enqueued: bool = Field(description="True if the CV scoring pipeline was dispatched.")


class TalentPoolSearchResultItem(BaseModel):
    """One ranked hit from a semantic pool search."""

    model_config = ConfigDict(from_attributes=True)

    candidate_id: uuid.UUID
    entry_id: uuid.UUID
    cv_document_id: uuid.UUID
    # Cosine similarity of the job's JD vs the candidate's current CV, 0-100
    # (higher = closer). Only present for job (semantic) search; None for a
    # free-text lexical search, which matches on role/skills/name not vectors.
    similarity_score: float | None = None
    # Title of the job the candidate was sourced from; None = direct upload.
    source_job_title: str | None = None
    added_at: datetime
    expires_at: datetime
    is_active: bool
    candidate: TalentPoolCandidateNested


class TalentPoolSearchResponse(BaseModel):
    """``GET /talent-pool/search`` body — query echoed back with ranked hits."""

    query: str
    items: list[TalentPoolSearchResultItem]
    total: int


# --- Cross-job candidate history --------------------------------------------


class CandidateHistoryScreeningAnswer(BaseModel):
    """One scored screening answer from a stint's WhatsApp conversation.

    Per-answer scores (``relevance_score`` / ``ai_score`` / ``rationale``)
    come from the inbound message row and are null until the answer-scoring
    task runs (or for non-answer turns).
    """

    question_index: int | None
    question: str | None
    answer: str | None
    relevance_score: int | None
    ai_score: int | None
    rationale: str | None


class CandidateHistoryScreening(BaseModel):
    """Compact WhatsApp screening summary for one stint.

    The authoritative transcript lives at ``GET /applications/{id}/whatsapp``;
    this is the per-stint digest HR sees in the history view — the conversation
    state plus the scored Q&A.
    """

    conversation_id: uuid.UUID
    state: WhatsAppConversationState
    answers: list[CandidateHistoryScreeningAnswer]
    created_at: datetime
    closed_at: datetime | None


class CandidateHistoryStint(BaseModel):
    """One application (stint) the candidate had on one job, any status.

    Spans every status — live (``active``), terminal (``rejected`` /
    ``accepted``), and ``archived`` (moved back to the pool). The scores and
    screening are the stint's own, computed against *that* job; authenticity is
    candidate-level and lives once on the parent response, not here.
    """

    application_id: uuid.UUID
    job_id: uuid.UUID
    job_title: str
    stage: ApplicationStage
    status: ApplicationStatus
    sourced_from_talent_pool: bool
    similarity_score: PercentStrOptional
    hard_filter_score: PercentStrOptional
    hard_filter_breakdown: dict[str, Any] | None
    # The append-only ``application_scores`` rows for this stint (similarity +
    # hard_filter), newest-first. Authenticity is *not* duplicated here.
    scores: list[ApplicationScoreResponse]
    screening: CandidateHistoryScreening | None
    # HR's optional justification for moving this stint back to the pool; set
    # only on archived stints where a reason was given, else NULL.
    move_to_pool_reason: str | None
    created_at: datetime
    stage_updated_at: datetime
    archived_at: datetime | None


class CandidateHistoryResponse(BaseModel):
    """``GET /talent-pool/candidates/{candidate_id}/history`` body.

    The candidate's entire journey across every job they've touched — each
    stint scored against its own job — so HR sees the full picture from the
    talent pool. ``in_pool`` reports whether the candidate currently sits in
    the active pool. Stints are newest-first by creation.
    """

    candidate: CandidateNested
    in_pool: bool
    stints: list[CandidateHistoryStint]
    total_stints: int
