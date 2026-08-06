"""Candidates table + functional unique index on (LOWER(email), phone_e164).

Revision ID: 0007_candidates
Revises: 0006_job_pipeline_columns
Create Date: 2026-05-23
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007_candidates"
down_revision: str | None = "0006_job_pipeline_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidates",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("phone_e164", sa.String(20), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column(
            "parsed_profile",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("authenticity_score", sa.Float(), nullable=True),
        sa.Column("authenticity_band", sa.String(32), nullable=True),
        sa.Column("authenticity_breakdown", JSONB, nullable=True),
        sa.Column("authenticity_computed_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    # Functional unique: case-insensitive on email + exact on phone_e164.
    # ``LOWER(email)`` matches the ORM-side normalization in
    # `Candidate._normalize_email`, so writes and dedup checks agree.
    op.execute(
        "CREATE UNIQUE INDEX uq_candidates_email_phone "
        "ON candidates ((LOWER(email)), phone_e164)"
    )
    # Reuses the trigger function created in migration 0004.
    op.execute(
        "CREATE TRIGGER candidates_set_updated_at "
        "BEFORE UPDATE ON candidates "
        "FOR EACH ROW EXECUTE FUNCTION kabil_set_updated_at()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS candidates_set_updated_at ON candidates")
    op.execute("DROP INDEX IF EXISTS uq_candidates_email_phone")
    op.drop_table("candidates")
