"""Add optional HR reason captured when moving an application to the talent pool.

When HR moves a candidate out of a job's pipeline back into the talent pool the
application is soft-archived. HR can now attach an optional free-text reason for
*why* — stored per-application (i.e. against that specific job stint) so it
surfaces in the candidate's cross-job history. Plain nullable column, like
``interview_comment``; NULL for every existing row and for moves with no reason.

Revision ID: 0027_move_to_pool_reason
Revises: 0026_job_description_ar
Create Date: 2026-07-11
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_move_to_pool_reason"
down_revision: str | None = "0026_job_description_ar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("move_to_pool_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "move_to_pool_reason")
