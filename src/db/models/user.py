"""User ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import UUID, DateTime, Enum, String, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from src.db.base import Base
from src.enums.roles import UserRole


class User(Base):
    """HR / admin user. Login identifier is ``email``."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            native_enum=False,
            length=32,
            create_constraint=False,
            # Store the enum's *value* ("admin"), not its *name* ("ADMIN"),
            # so manual inserts and external readers see the same string the
            # architecture doc specifies.
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    @validates("email")
    def _normalize_email(self, _key: str, value: str) -> str:
        return value.strip().lower()

    def __repr__(self) -> str:
        # password_hash is intentionally omitted so logs / reprs can't leak it.
        return (
            f"User(id={self.id!r}, email={self.email!r}, "
            f"full_name={self.full_name!r}, role={self.role!r}, "
            f"created_at={self.created_at!r}, last_login_at={self.last_login_at!r})"
        )
