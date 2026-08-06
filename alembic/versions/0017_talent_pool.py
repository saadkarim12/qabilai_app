"""talent_pool_entries table + applications.sourced_from_talent_pool flag.

Backs HR sourcing a pooled candidate onto a job. The entry is candidate-level
(UNIQUE candidate_id); the application flag records that a given application was
created via sourcing, which selects the talent-pool WhatsApp greeting at L2.

Revision ID: 0017_talent_pool
Revises: 0016_whatsapp_conversations
Create Date: 2026-06-09
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_talent_pool"
down_revision: str | None = "0016_whatsapp_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "talent_pool_entries",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "candidate_id",
            sa.UUID(),
            sa.ForeignKey(
                "candidates.id",
                ondelete="CASCADE",
                name="fk_talent_pool_entries_candidate",
            ),
            nullable=False,
        ),
        sa.Column(
            "source_job_id",
            sa.UUID(),
            sa.ForeignKey(
                "jobs.id",
                ondelete="SET NULL",
                name="fk_talent_pool_entries_source_job",
            ),
            nullable=True,
        ),
        sa.Column(
            "added_by",
            sa.UUID(),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_talent_pool_entries_added_by",
            ),
            nullable=True,
        ),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.UniqueConstraint("candidate_id", name="uq_talent_pool_entries_candidate"),
    )
    op.create_index(
        "ix_talent_pool_entries_active_added_at",
        "talent_pool_entries",
        ["is_active", sa.text("added_at DESC")],
    )

    op.add_column(
        "applications",
        sa.Column(
            "sourced_from_talent_pool",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("applications", "sourced_from_talent_pool")
    op.drop_index(
        "ix_talent_pool_entries_active_added_at", table_name="talent_pool_entries"
    )
    op.drop_table("talent_pool_entries")
