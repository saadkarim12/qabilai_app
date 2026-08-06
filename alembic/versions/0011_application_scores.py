"""application_scores audit table.

Appends rows from Step 3.9 (similarity) and Step 4.3 (hard_filter). The
``(application_id, computed_at DESC)`` index serves the per-application
score-history read used by the detail endpoint.

Revision ID: 0011_application_scores
Revises: 0010_app_pipeline_columns
Create Date: 2026-05-23
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_application_scores"
down_revision: str | None = "0010_app_pipeline_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "application_scores",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "application_id",
            sa.UUID(),
            sa.ForeignKey(
                "applications.id",
                ondelete="CASCADE",
                name="fk_application_scores_application",
            ),
            nullable=False,
        ),
        sa.Column("score_type", sa.String(32), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column(
            "breakdown",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("model_used", sa.String(64), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_application_scores_app_computed_at",
        "application_scores",
        ["application_id", sa.text("computed_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_application_scores_app_computed_at", table_name="application_scores")
    op.drop_table("application_scores")
