"""Public upload tokens table.

Revision ID: 0005_public_upload_tokens
Revises: 0004_jobs
Create Date: 2026-05-23
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_public_upload_tokens"
down_revision: str | None = "0004_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "public_upload_tokens",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "job_id",
            sa.UUID(),
            sa.ForeignKey(
                "jobs.id",
                ondelete="CASCADE",
                name="fk_public_upload_tokens_job_id",
            ),
            nullable=False,
        ),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint(
        "uq_public_upload_tokens_slug", "public_upload_tokens", ["slug"]
    )
    op.create_index(
        "ix_public_upload_tokens_job_id_is_active",
        "public_upload_tokens",
        ["job_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_public_upload_tokens_job_id_is_active",
        table_name="public_upload_tokens",
    )
    op.drop_constraint(
        "uq_public_upload_tokens_slug", "public_upload_tokens", type_="unique"
    )
    op.drop_table("public_upload_tokens")
