"""Phase 4 scoring + Step 3.9 pipeline columns on applications.

Adds the columns the CV processing chain writes (``similarity_score``,
``pipeline_status``) plus the rest of the Phase 4 schema the architecture
doc lists on the ``applications`` table (``hard_filter_score``,
``hard_filter_breakdown``, ``stage_updated_at``). Pulling them all
forward in one migration avoids a second ALTER pass during Step 4.x.

Revision ID: 0010_app_pipeline_columns
Revises: 0009_applications
Create Date: 2026-05-23
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_app_pipeline_columns"
down_revision: str | None = "0009_applications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("similarity_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("hard_filter_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column(
            "hard_filter_breakdown",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
    )
    op.add_column(
        "applications",
        sa.Column(
            "pipeline_status",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "applications",
        sa.Column(
            "stage_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_column("applications", "stage_updated_at")
    op.drop_column("applications", "pipeline_status")
    op.drop_column("applications", "hard_filter_breakdown")
    op.drop_column("applications", "hard_filter_score")
    op.drop_column("applications", "similarity_score")
