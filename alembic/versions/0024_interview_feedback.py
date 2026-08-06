"""Add HR-entered interview feedback columns to applications.

HR records a post-interview evaluation — a manual mark in [0, 100] plus an
optional free-text comment — once an application reaches the ``interview``
stage. These have no model / prompt behind them (unlike the automated
similarity / hard_filter / authenticity scores), so they live as plain nullable
columns on ``applications`` rather than in the append-only ``application_scores``
audit table. All NULL for existing rows until HR submits feedback.

Revision ID: 0024_interview_feedback
Revises: 0023_drop_job_screening_fields
Create Date: 2026-07-01
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_interview_feedback"
down_revision: str | None = "0023_drop_job_screening_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("interview_score", sa.Float(), nullable=True))
    op.add_column("applications", sa.Column("interview_comment", sa.Text(), nullable=True))
    op.add_column(
        "applications",
        sa.Column("interview_scored_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("applications", "interview_scored_at")
    op.drop_column("applications", "interview_comment")
    op.drop_column("applications", "interview_score")
