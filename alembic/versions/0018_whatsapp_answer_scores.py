"""whatsapp_messages answer-score columns (Step 5.4).

Per-answer screening scores: each inbound free-text answer is rated 0-10 for
relevance to the question and for how AI-generated it reads, with a short
HR-facing rationale. All three columns are nullable — they are set only on
inbound answer rows (and left null if scoring failed), so every other message
row keeps them null.

Revision ID: 0018_whatsapp_answer_scores
Revises: 0017_talent_pool
Create Date: 2026-06-09
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_whatsapp_answer_scores"
down_revision: str | None = "0017_talent_pool"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_messages",
        sa.Column("answer_relevance_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "whatsapp_messages",
        sa.Column("answer_ai_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "whatsapp_messages",
        sa.Column("answer_score_rationale", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_messages", "answer_score_rationale")
    op.drop_column("whatsapp_messages", "answer_ai_score")
    op.drop_column("whatsapp_messages", "answer_relevance_score")
