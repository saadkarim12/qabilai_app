"""Job ORM model.

A Job represents one open role. The columns capture everything the Sonnet
hard-filter scorer (Step 4.3) and the LangGraph application agent (Step 5)
need: location, salary band, must-haves, nice-to-haves, and the 1536-d JD
embedding used for cosine pre-filtering of incoming CVs.

The HNSW index on ``jd_embedding`` and the ``updated_at`` trigger live in
Alembic migration ``0004_jobs``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import UUID, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base
from src.db.types import Vector
from src.enums.jobs import (
    EmploymentType,
    JobStatus,
    NoticePeriod,
    VisaRequirement,
    WorkMode,
)

# JD embeddings come from OpenAI ``text-embedding-3-small`` (Step 2.5 pins
# the model). 1536 dims keeps the HNSW index footprint predictable.
JD_EMBEDDING_DIM: Final[int] = 1536


def _enum_column(enum_cls: type[StrEnum]) -> Enum:
    """Varchar-backed enum column that stores ``.value`` (not ``.name``)."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=32,
        create_constraint=False,
        values_callable=lambda cls: [e.value for e in cls],
    )


class Job(Base):
    """A job opening posted by an HR admin."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_jobs_created_by"),
        nullable=False,
    )

    # --- Identity / location ----------------------------------------------
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    hiring_company: Mapped[str] = mapped_column(String(255), nullable=False)
    # ISO-3166 alpha-2 (e.g. "ID", "US").
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)

    # --- Engagement / mode ------------------------------------------------
    employment_type: Mapped[EmploymentType] = mapped_column(
        _enum_column(EmploymentType), nullable=False
    )
    work_mode: Mapped[WorkMode] = mapped_column(_enum_column(WorkMode), nullable=False)

    # --- Compensation -----------------------------------------------------
    # ISO-4217 (e.g. "USD", "IDR").
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    min_salary: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_salary: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Hiring constraints ----------------------------------------------
    notice_period: Mapped[NoticePeriod | None] = mapped_column(
        _enum_column(NoticePeriod), nullable=True
    )
    min_experience_years: Mapped[int] = mapped_column(Integer, nullable=False)
    required_skills: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    preferred_skills: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    visa_requirement: Mapped[VisaRequirement | None] = mapped_column(
        _enum_column(VisaRequirement), nullable=True
    )
    nationality_preference: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    languages_required: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    ai_verify_flags: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # --- Content + AI artifacts ------------------------------------------
    job_description: Mapped[str] = mapped_column(Text, nullable=False)
    # Native Gulf-Arabic rendering of the JD (not a translation). Populated by
    # the AI JD Builder alongside ``job_description``; NULL for older jobs and
    # for jobs whose description was written manually without the builder.
    job_description_ar: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Populated asynchronously after job creation (Step 2.5 background task).
    jd_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(JD_EMBEDDING_DIM), nullable=True
    )
    whatsapp_questions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # --- Pipeline (Step 2.6) ---------------------------------------------
    # ``pipeline_status`` is a JSONB blob keyed by ``PipelineStep`` value
    # ("embedding", "questions") whose values are ``PipelineStepState``
    # strings. A missing key means "never run". Error messages live in
    # parallel ``{step}_error`` keys.
    pipeline_status: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Set to True only after every pipeline step is ``ok``; gates whether
    # the public /apply/{slug} route accepts submissions in Step 5.
    ready_for_applications: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    # --- Lifecycle --------------------------------------------------------
    status: Mapped[JobStatus] = mapped_column(
        _enum_column(JobStatus),
        nullable=False,
        server_default=JobStatus.DRAFT.value,
    )
    public_slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:
        return (
            f"Job(id={self.id!r}, title={self.title!r}, status={self.status!r}, "
            f"hiring_company={self.hiring_company!r}, created_at={self.created_at!r})"
        )
