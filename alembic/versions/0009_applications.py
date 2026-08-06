"""Applications table + unique (candidate_id, job_id) for idempotent re-apply.

Revision ID: 0009_applications
Revises: 0008_cv_documents
Create Date: 2026-05-23
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

from src.enums.applications import ApplicationStage, ApplicationStatus

revision: str = "0009_applications"
down_revision: str | None = "0008_cv_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "applications",
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
                name="fk_applications_candidate",
            ),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.UUID(),
            sa.ForeignKey(
                "jobs.id",
                ondelete="CASCADE",
                name="fk_applications_job",
            ),
            nullable=False,
        ),
        sa.Column(
            "cv_document_id",
            sa.UUID(),
            sa.ForeignKey(
                "cv_documents.id",
                ondelete="RESTRICT",
                name="fk_applications_cv_document",
            ),
            nullable=False,
        ),
        sa.Column(
            "stage",
            sa.String(32),
            nullable=False,
            server_default=sa.text(f"'{ApplicationStage.VECTOR_SCREEN.value}'"),
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text(f"'{ApplicationStatus.ACTIVE.value}'"),
        ),
        sa.Column(
            "consent_context",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "candidate_id",
            "job_id",
            name="uq_applications_candidate_job",
        ),
    )
    # Lookup index for "all applications for a job" listing (Step 4.1).
    op.create_index("ix_applications_job_id", "applications", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_applications_job_id", table_name="applications")
    op.drop_table("applications")
