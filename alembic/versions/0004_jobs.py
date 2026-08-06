"""Jobs table + HNSW index + shared updated_at trigger function.

Revision ID: 0004_jobs
Revises: 0003_sessions
Create Date: 2026-05-23
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from src.db.models.job import JD_EMBEDDING_DIM
from src.db.types import Vector
from src.enums.jobs import JobStatus

revision: str = "0004_jobs"
down_revision: str | None = "0003_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Shared updated_at trigger function. ``jobs`` is the first table with
    # an ``updated_at`` column; later migrations reuse the same function on
    # the candidates / applications tables.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION kabil_set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = clock_timestamp();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.create_table(
        "jobs",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_by",
            sa.UUID(),
            sa.ForeignKey(
                "users.id", ondelete="RESTRICT", name="fk_jobs_created_by"
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("hiring_company", sa.String(255), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("employment_type", sa.String(32), nullable=False),
        sa.Column("work_mode", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("min_salary", sa.Integer, nullable=True),
        sa.Column("max_salary", sa.Integer, nullable=True),
        sa.Column("notice_period", sa.String(32), nullable=True),
        sa.Column("min_experience_years", sa.Integer, nullable=False),
        sa.Column(
            "required_skills",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "preferred_skills",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("visa_requirement", sa.String(32), nullable=True),
        sa.Column(
            "nationality_preference",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "languages_required",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "ai_verify_flags",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("job_description", sa.Text, nullable=False),
        sa.Column("jd_embedding", Vector(JD_EMBEDDING_DIM), nullable=True),
        sa.Column(
            "whatsapp_questions",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=JobStatus.DRAFT.value,
        ),
        sa.Column("public_slug", sa.String(64), nullable=False),
        sa.Column(
            "closed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
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
    op.create_unique_constraint("uq_jobs_public_slug", "jobs", ["public_slug"])
    op.execute(
        "CREATE INDEX jobs_jd_embedding_hnsw_idx "
        "ON jobs USING hnsw (jd_embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE TRIGGER jobs_set_updated_at "
        "BEFORE UPDATE ON jobs "
        "FOR EACH ROW EXECUTE FUNCTION kabil_set_updated_at()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS jobs_set_updated_at ON jobs")
    op.execute("DROP INDEX IF EXISTS jobs_jd_embedding_hnsw_idx")
    op.drop_constraint("uq_jobs_public_slug", "jobs", type_="unique")
    op.drop_table("jobs")
    op.execute("DROP FUNCTION IF EXISTS kabil_set_updated_at()")
