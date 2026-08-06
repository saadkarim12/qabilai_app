"""Add native Gulf-Arabic job description column to jobs.

The AI JD Builder now drafts the JD bilingually — the English prose in
``jobs.job_description`` and a native (not machine-translated) Gulf-Arabic
rendering here. Nullable: existing jobs and manually-written descriptions
have no Arabic version until the builder regenerates them.

Revision ID: 0026_job_description_ar
Revises: 0025_application_authenticity
Create Date: 2026-07-03
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_job_description_ar"
down_revision: str | None = "0025_application_authenticity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("job_description_ar", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "job_description_ar")
