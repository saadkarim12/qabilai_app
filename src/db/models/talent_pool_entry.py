"""Talent-pool entry ORM model (Phase 7, pulled forward for WhatsApp sourcing).

A :class:`TalentPoolEntry` marks a candidate as kept in the talent pool — a
holding area of good candidates HR can later *source* onto a job. It is
**candidate-level** (UNIQUE on ``candidate_id``): a candidate is in the pool
at most once, regardless of how many jobs they've touched.

``source_job_id`` records where they came from (nullable for a direct upload).
``expires_at`` defaults to ``added_at + talent_pool_expiry_days`` (config), and
``is_active`` lets HR deactivate an entry without deleting the history.

Only the slice needed for WhatsApp sourcing is built now (add / list / source);
the Phase-7 semantic search + expiry sweep come later.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import UUID, Boolean, DateTime, ForeignKey, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class TalentPoolEntry(Base):
    """One candidate's membership in the talent pool."""

    __tablename__ = "talent_pool_entries"
    __table_args__ = (UniqueConstraint("candidate_id", name="uq_talent_pool_entries_candidate"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, server_default=text("gen_random_uuid()")
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey(
            "candidates.id",
            ondelete="CASCADE",
            name="fk_talent_pool_entries_candidate",
        ),
        nullable=False,
    )
    # Where the candidate was sourced from; NULL for a direct (no-job) upload.
    source_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        ForeignKey("jobs.id", ondelete="SET NULL", name="fk_talent_pool_entries_source_job"),
        nullable=True,
    )
    # Nullable + SET NULL so deleting the HR user preserves the pool entry.
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_talent_pool_entries_added_by"),
        nullable=True,
    )

    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    def __repr__(self) -> str:
        return (
            f"TalentPoolEntry(id={self.id!r}, candidate_id={self.candidate_id!r}, "
            f"is_active={self.is_active!r}, expires_at={self.expires_at!r})"
        )
