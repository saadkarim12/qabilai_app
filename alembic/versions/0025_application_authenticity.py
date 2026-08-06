"""Add job-aware authenticity columns to applications.

Authenticity is now scored in two places: the candidate keeps its
job-agnostic *baseline* (five signals) in ``candidates.authenticity_*``,
and each application gets a six-signal, job-aware verdict (adds JD Keyword
Mirroring, calibrated against the job) in these new columns. Written by the
CV pipeline's authenticity step. All NULL for existing rows until the step
re-runs for that application.

The band column is a varchar (matches the model's ``native_enum=False``
enum, storing the enum ``.value``); the breakdown is JSONB, mirroring
``candidates.authenticity_breakdown``.

Revision ID: 0025_application_authenticity
Revises: 0024_interview_feedback
Create Date: 2026-07-03
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0025_application_authenticity"
down_revision: str | None = "0024_interview_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("authenticity_score", sa.Float(), nullable=True))
    op.add_column(
        "applications",
        sa.Column("authenticity_band", sa.String(length=32), nullable=True),
    )
    op.add_column("applications", sa.Column("authenticity_breakdown", JSONB(), nullable=True))
    op.add_column(
        "applications",
        sa.Column("authenticity_computed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("applications", "authenticity_computed_at")
    op.drop_column("applications", "authenticity_breakdown")
    op.drop_column("applications", "authenticity_band")
    op.drop_column("applications", "authenticity_score")
