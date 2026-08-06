"""Public upload token ORM model.

One row per slug ever issued for a job. The slug is what appears in the
public ``/apply/{slug}`` URL the HM shares with candidates; rotating it
(``is_active = false`` on the old row, new row for the new slug) is the
only way to invalidate stale links. Old rows are retained for audit.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import UUID, Boolean, DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class PublicUploadToken(Base):
    """A slug that authorizes anonymous CV upload for a specific Job."""

    __tablename__ = "public_upload_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, server_default=text("gen_random_uuid()")
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("jobs.id", ondelete="CASCADE", name="fk_public_upload_tokens_job_id"),
        nullable=False,
    )
    # Generated via ``secrets.token_urlsafe`` (Step 2.5); never derived
    # from any user-visible field.
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:
        return (
            f"PublicUploadToken(id={self.id!r}, job_id={self.job_id!r}, "
            f"slug={self.slug!r}, is_active={self.is_active!r})"
        )
