"""jobs.screening_fields + reset stale whatsapp_questions categories.

Adds ``jobs.screening_fields`` (JSONB array of field keys HR ticked to "ask on
WhatsApp"), mirroring the ``required_skills`` column shape.

The ``QuestionCategory`` enum was reduced to three values
(commitment / salary / background_validation); any pre-existing
``whatsapp_questions`` rows holding the removed categories
(skill_assessment / logistics / motivation) would fail to deserialize through
the response schema, so this migration clears them to ``'[]'`` and resets the
``questions`` pipeline step. Jobs simply regenerate their questions on next
open. Safe for the current pre-production data set.

Revision ID: 0022_job_screening_fields
Revises: 0021_interview_booking_details
Create Date: 2026-06-24
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0022_job_screening_fields"
down_revision: str | None = "0021_interview_booking_details"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keep the JSONB ``'[]'`` server default (matches required_skills /
    # preferred_skills): the ORM relies on it for rows created without an
    # explicit value.
    op.add_column(
        "jobs",
        sa.Column(
            "screening_fields",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    # Reset questions that may carry the now-removed category values so the
    # detail/response schema can deserialize every row. Cleared lists regenerate
    # on the next job open via the questions pipeline step.
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
    op.drop_column("jobs", "screening_fields")
