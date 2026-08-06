"""Application ORM model.

One row per candidate-per-job. Created by the public upload endpoint
(Step 3.8) at ``stage=VECTOR_SCREEN, status=ACTIVE`` and updated as the
candidate moves through the pipeline.

Key invariants:

* **Idempotent re-apply** — a partial unique index on
  ``(candidate_id, job_id) WHERE status <> 'archived'`` means the *same*
  candidate (same email + phone) re-applying to the *same* job gets the
  existing *live* application row back, satisfying the doc's "same
  candidate applying twice to same job: returns 202 with original ref"
  acceptance criterion. Archived stints (move-to-pool) are excluded, so a
  candidate sourced back onto a job they previously left starts a fresh
  application while their prior stint is retained as history.
* **CV history** — ``cv_document_id`` points at the *current* CV; when
  a candidate uploads a fresh CV (different sha256) the
  ``cv_documents.is_current`` flag rotates but the application keeps the
  same row, just pointing at the new CV.
* **Consent audit** — ``consent_context`` is the verbatim ip / user-agent
  snapshot captured at upload time. Required by the doc's consent
  acceptance criterion ("Consent record stored with IP + user agent").

Step 3.9 pulled the full Phase 4 scoring schema forward (per architecture
doc Step 4.1's ``applications`` table): ``similarity_score`` (Step 3.9
writes), ``hard_filter_score`` + ``hard_filter_breakdown`` (Step 4.3
will write), ``stage_updated_at`` (Step 4.2 will bump), and
``pipeline_status`` JSONB (Step 3.9's per-step audit trail, mirrors the
job pipeline's column of the same name).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    UUID,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.authenticity import AuthenticityBand


def _enum_column(enum_cls: type[StrEnum]) -> Enum:
    """Varchar-backed enum column that stores ``.value`` (not ``.name``)."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=32,
        create_constraint=False,
        values_callable=lambda cls: [e.value for e in cls],
    )


class Application(Base):
    """One candidate's submission to one job."""

    __tablename__ = "applications"
    __table_args__ = (
        # Live-uniqueness, not absolute uniqueness: at most one *non-archived*
        # application per (candidate, job). Archived stints are excluded so a
        # candidate who left a job (move-to-pool) can be sourced back onto it
        # for a fresh application; every past stint is retained as history.
        Index(
            "uq_applications_candidate_job_live",
            "candidate_id",
            "job_id",
            unique=True,
            postgresql_where=text(f"status <> '{ApplicationStatus.ARCHIVED.value}'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, server_default=text("gen_random_uuid()")
    )

    # --- FKs ----------------------------------------------------------------
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("candidates.id", ondelete="CASCADE", name="fk_applications_candidate"),
        nullable=False,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("jobs.id", ondelete="CASCADE", name="fk_applications_job"),
        nullable=False,
    )
    cv_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("cv_documents.id", ondelete="RESTRICT", name="fk_applications_cv_document"),
        nullable=False,
    )

    # --- Lifecycle ----------------------------------------------------------
    stage: Mapped[ApplicationStage] = mapped_column(
        _enum_column(ApplicationStage),
        nullable=False,
        server_default=text(f"'{ApplicationStage.VECTOR_SCREEN.value}'"),
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        _enum_column(ApplicationStatus),
        nullable=False,
        server_default=text(f"'{ApplicationStatus.ACTIVE.value}'"),
    )

    # --- Origin -------------------------------------------------------------
    # True when this application was created by HR *sourcing* the candidate
    # from the talent pool (vs a public apply / bulk upload). Drives the
    # different L2 WhatsApp greeting — "we came across your profile" rather
    # than "thanks for applying". Defaults false for every existing row.
    sourced_from_talent_pool: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    # --- Consent audit ------------------------------------------------------
    # JSONB instead of a fixed schema so we can extend the captured set
    # (locale, referrer, fingerprint, ...) without a migration. The
    # ``ip``/``user_agent``/``accepted_at`` keys are the minimum the doc
    # requires and the intake service writes them on every insert.
    consent_context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Scalar mirror of ``consent_context.accepted_at`` so we can index /
    # range-query on consent time without unpacking JSONB.
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # --- Scoring (Phase 4 schema, pulled forward in Step 3.9) --------------
    # Cosine similarity between the candidate's current CV embedding and
    # the job's JD embedding, as a percentage in [0, 100] (higher = better
    # match). NULL until the Step 3.9 ``score_similarity_task`` writes it.
    # Denormalized here for fast list ordering — the full audit row lives in
    # ``application_scores`` once Step 4.1 lands.
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Hard-filter aggregate score (Step 4.3). NULL until that step writes.
    hard_filter_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Per-signal hard-filter scores + reasoning. NULL until Step 4.3.
    hard_filter_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # --- Authenticity (job-aware, all six signals) --------------------------
    # The application-level authenticity verdict: the six-signal, job-aware
    # blend (adds JD Keyword Mirroring on top of the five job-agnostic
    # signals). Written by the CV pipeline's authenticity step keyed on this
    # application (which loads the job for context). Distinct from the
    # candidate's job-agnostic *baseline* score (``candidates.authenticity_*``),
    # which the talent pool surfaces. NULL until the step runs.
    authenticity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    authenticity_band: Mapped[AuthenticityBand | None] = mapped_column(
        _enum_column(AuthenticityBand), nullable=True
    )
    authenticity_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    authenticity_computed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Interview evaluation (HR-entered, post-interview) ------------------
    # A manual mark in [0, 100] HR records after the interview, an optional
    # free-text comment, and when it was (last) set. NULL until HR submits
    # feedback at the ``interview`` stage. Unlike the automated scores above,
    # these have no model / prompt behind them, so they live as plain columns
    # here rather than in the append-only ``application_scores`` audit table.
    interview_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    interview_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    interview_scored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Pipeline (Step 3.9) ------------------------------------------------
    # JSONB blob keyed by ``CvPipelineStep`` value
    # ("extract_text", "parse_cv", "authenticity", "embed_cv", "similarity",
    # "auto_reject") whose values are ``CvPipelineStepState`` strings.
    # A missing key means "never run". Error messages live in parallel
    # ``{step}_error`` keys. Mirrors ``jobs.pipeline_status``.
    pipeline_status: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # --- Timestamps ---------------------------------------------------------
    # Bumped by Step 4.2 stage-transition logic; defaults to now() at insert
    # so a freshly-created row's stage timing is meaningful right away.
    stage_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # Set when the application is archived (moved back to the talent pool);
    # NULL for every live / rejected / accepted row. Orders a candidate's
    # cross-job history by when each stint ended.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Optional HR-supplied justification captured when the application is moved
    # back to the talent pool (soft-archived). NULL for live rows and for
    # archived stints where HR gave no reason. Surfaced per-stint in the
    # candidate's cross-job history; also mirrored into the audit log.
    move_to_pool_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:
        return (
            f"Application(id={self.id!r}, candidate_id={self.candidate_id!r}, "
            f"job_id={self.job_id!r}, stage={self.stage!r}, status={self.status!r})"
        )
