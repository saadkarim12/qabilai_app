"""CV documents table + HNSW index + partial unique on (candidate_id) WHERE is_current.

Revision ID: 0008_cv_documents
Revises: 0007_candidates
Create Date: 2026-05-23
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

from src.db.models.cv_document import CV_EMBEDDING_DIM
from src.db.types import Vector

revision: str = "0008_cv_documents"
down_revision: str | None = "0007_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cv_documents",
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
                name="fk_cv_documents_candidate",
            ),
            nullable=False,
        ),
        sa.Column("blob_url", sa.String(500), nullable=False),
        sa.Column("blob_sha256", sa.String(64), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("cv_embedding", Vector(CV_EMBEDDING_DIM), nullable=True),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.execute(
        "CREATE INDEX cv_documents_cv_embedding_hnsw_idx "
        "ON cv_documents USING hnsw (cv_embedding vector_cosine_ops)"
    )
    # Partial UNIQUE: exactly one current CV per candidate. Multiple
    # non-current rows are fine, so older CVs can sit alongside the live
    # one for talent-pool history.
    op.execute(
        "CREATE UNIQUE INDEX uq_cv_documents_current_per_candidate "
        "ON cv_documents (candidate_id) WHERE is_current"
    )
    # Plain btree lookup index covers non-current rows the partial index
    # can't help with (e.g. listing a candidate's CV history).
    op.create_index("ix_cv_documents_candidate_id", "cv_documents", ["candidate_id"])


def downgrade() -> None:
    op.drop_index("ix_cv_documents_candidate_id", table_name="cv_documents")
    op.execute("DROP INDEX IF EXISTS uq_cv_documents_current_per_candidate")
    op.execute("DROP INDEX IF EXISTS cv_documents_cv_embedding_hnsw_idx")
    op.drop_table("cv_documents")
