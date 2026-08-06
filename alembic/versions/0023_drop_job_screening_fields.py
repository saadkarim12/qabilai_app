"""Drop jobs.screening_fields; reset stale whatsapp_questions.

WhatsApp screening questions are no longer HR-selected per field. The fixed
question set is sourced server-side on job open
(``src.enums.fixed_screening_questions``), so the ``screening_fields`` column
added in 0022 is removed.

Existing ``whatsapp_questions`` rows were generated under the old field-driven
model (and lack the new ``ai_verifies_response`` flag / fixed wording), so this
migration clears them to ``'[]'`` and resets the ``questions`` pipeline step;
jobs regenerate their fixed + AI questions on the next open. Safe for the
current pre-production data set.

Revision ID: 0023_drop_job_screening_fields
Revises: 0022_job_screening_fields
Create Date: 2026-06-25
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0023_drop_job_screening_fields"
down_revision: str | None = "0022_job_screening_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("jobs", "screening_fields")

    # Clear questions produced under the previous field-driven model so every
    # row deserializes cleanly and regenerates with the fixed set on next open.
    op.execute(
        """
        UPDATE jobs
        SET whatsapp_questions = '[]'::jsonb,
            pipeline_status = pipeline_status - 'questions' - 'questions_error',
            ready_for_applications = false
        WHERE whatsapp_questions <> '[]'::jsonb
        """
    )


def downgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "screening_fields",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
