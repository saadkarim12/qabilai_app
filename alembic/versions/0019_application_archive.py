"""applications.archived_at + live-uniqueness partial index for re-sourcing.

Soft-archive replaces the hard-delete in move-to-pool: a pooled candidate's
application (and its ``application_scores`` + WhatsApp transcript) is retained
as cross-job history instead of being cascade-deleted. The absolute
``UNIQUE(candidate_id, job_id)`` is relaxed to a partial unique index that
ignores archived stints, so the candidate can be sourced back onto the same
job for a fresh application.

Revision ID: 0019_application_archive
Revises: 0018_whatsapp_answer_scores
Create Date: 2026-06-14
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

from src.enums.applications import ApplicationStatus

revision: str = "0019_application_archive"
down_revision: str | None = "0018_whatsapp_answer_scores"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ARCHIVED = ApplicationStatus.ARCHIVED.value


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Swap absolute uniqueness for live-uniqueness: only non-archived rows
    # collide, so re-sourcing onto a previously-left job is allowed and every
    # archived stint is preserved.
    op.drop_constraint("uq_applications_candidate_job", "applications", type_="unique")
    op.create_index(
        "uq_applications_candidate_job_live",
        "applications",
        ["candidate_id", "job_id"],
        unique=True,
        postgresql_where=sa.text(f"status <> '{_ARCHIVED}'"),
    )


def downgrade() -> None:
    # Archived rows must be removed before the absolute unique constraint can
    # be restored, otherwise duplicate (candidate, job) pairs would violate it.
    op.execute(sa.text(f"DELETE FROM applications WHERE status = '{_ARCHIVED}'"))
    op.drop_index("uq_applications_candidate_job_live", table_name="applications")
    op.create_unique_constraint(
        "uq_applications_candidate_job",
        "applications",
        ["candidate_id", "job_id"],
    )
    op.drop_column("applications", "archived_at")
