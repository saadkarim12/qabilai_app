"""Auth session ORM model.

One row per logged-in session. The raw JWT is never stored — only its
SHA-256 hex digest (``token_hash``), which Step 1.3's auth route writes
when the session is created.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import UUID, DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class AuthSession(Base):
    """Login session — class name avoids clashing with ``sqlalchemy.orm.Session``."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_sessions_user_id"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        # token_hash is non-secret (already a hash), but kept short for readability.
        return (
            f"AuthSession(id={self.id!r}, user_id={self.user_id!r}, "
            f"expires_at={self.expires_at!r}, revoked_at={self.revoked_at!r})"
        )
