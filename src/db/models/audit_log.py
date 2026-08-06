"""``AuditLog`` ORM model — generic per-action audit trail.

The architecture doc's ``audit_logs`` schema:

    | user_id      | UUID FK users (nullable for system actions)  |
    | entity_type  | varchar ('application' / 'job' / ...)        |
    | entity_id    | UUID                                         |
    | action       | varchar ('stage_changed' / ...)              |
    | before_state | jsonb (nullable)                             |
    | after_state  | jsonb                                        |
    | ip_address   | varchar (nullable)                           |
    | created_at   | timestamptz                                  |

Step 4.1 ships the table + read endpoint (``GET /applications/{id}/
audit-log``); the first writes land in Step 4.2 when stage/status
transitions become PATCHable.

``entity_id`` is not FK'd to any one table because the relationship is
polymorphic (an audit row may point at an application, a job, a
candidate, ...). Querying by (entity_type, entity_id) is the supported
read path; the index on those two columns + ``created_at DESC`` makes
the chronological read for one entity a single index seek.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import UUID, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

ENTITY_TYPE_MAX_LENGTH: Final[int] = 32
ACTION_MAX_LENGTH: Final[int] = 64
IP_ADDRESS_MAX_LENGTH: Final[int] = 45  # IPv6 max length


class AuditLog(Base):
    """One HR action or system state-change record."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, server_default=text("gen_random_uuid()")
    )

    # Nullable: system actions (cron jobs, auto-rejection) have no user.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_audit_logs_user"),
        nullable=True,
    )

    entity_type: Mapped[str] = mapped_column(String(ENTITY_TYPE_MAX_LENGTH), nullable=False)
    # Polymorphic — no FK; index on (entity_type, entity_id) is the
    # query path. Joining back to the source row is the caller's job.
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)
    action: Mapped[str] = mapped_column(String(ACTION_MAX_LENGTH), nullable=False)

    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    ip_address: Mapped[str | None] = mapped_column(String(IP_ADDRESS_MAX_LENGTH), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:
        return (
            f"AuditLog(id={self.id!r}, entity_type={self.entity_type!r}, "
            f"entity_id={self.entity_id!r}, action={self.action!r}, "
            f"user_id={self.user_id!r})"
        )
