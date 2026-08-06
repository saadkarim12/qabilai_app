"""Job pipeline columns: pipeline_status JSONB + ready_for_applications BOOL.

Revision ID: 0006_job_pipeline_columns
Revises: 0005_public_upload_tokens
Create Date: 2026-05-23
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0006_job_pipeline_columns"
down_revision: str | None = "0005_public_upload_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "pipeline_status",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "ready_for_applications",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("jobs", "ready_for_applications")
    op.drop_column("jobs", "pipeline_status")
