"""audit_logs table — generic HR/system action audit.

Step 4.1 introduces the table and the read endpoint; Step 4.2 wires the
first writes on stage/status transitions. The (entity_type, entity_id,
created_at DESC) composite index serves the per-entity chronological
read used by ``GET /applications/{id}/audit-log``.

Revision ID: 0012_audit_logs
Revises: 0011_application_scores
Create Date: 2026-05-23
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_audit_logs"
down_revision: str | None = "0011_application_scores"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_audit_logs_user",
            ),
            nullable=True,
        ),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column(
            "before_state",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "after_state",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_audit_logs_entity_chrono",
        "audit_logs",
        ["entity_type", "entity_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_entity_chrono", table_name="audit_logs")
    op.drop_table("audit_logs")
