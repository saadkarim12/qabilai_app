"""whatsapp_conversations + whatsapp_messages tables.

Backs the Step 5.3 screening conversation: one conversation per application
(state-machine cursor + structured answers) and a verbatim message transcript
HR renders on the FE. The unique ``wa_message_id`` index dedupes redelivered
Meta webhooks; the ``(conversation_id, created_at)`` index serves the ordered
transcript read; ``(candidate_id, created_at DESC)`` serves the per-candidate
history read.

Revision ID: 0016_whatsapp_conversations
Revises: 0015_hard_filter_weight_pct
Create Date: 2026-06-09
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_whatsapp_conversations"
down_revision: str | None = "0015_hard_filter_weight_pct"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_conversations",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "application_id",
            sa.UUID(),
            sa.ForeignKey(
                "applications.id",
                ondelete="CASCADE",
                name="fk_whatsapp_conversations_application",
            ),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            sa.UUID(),
            sa.ForeignKey(
                "candidates.id",
                ondelete="CASCADE",
                name="fk_whatsapp_conversations_candidate",
            ),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.UUID(),
            sa.ForeignKey(
                "jobs.id",
                ondelete="CASCADE",
                name="fk_whatsapp_conversations_job",
            ),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'awaiting_interest'"),
        ),
        sa.Column("current_question_index", sa.Integer(), nullable=True),
        sa.Column(
            "answers",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("phone_e164", sa.String(20), nullable=False),
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
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("application_id", name="uq_whatsapp_conversations_application"),
    )
    op.create_index(
        "ix_whatsapp_conversations_candidate_created_at",
        "whatsapp_conversations",
        ["candidate_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "whatsapp_messages",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "conversation_id",
            sa.UUID(),
            sa.ForeignKey(
                "whatsapp_conversations.id",
                ondelete="CASCADE",
                name="fk_whatsapp_messages_conversation",
            ),
            nullable=False,
        ),
        sa.Column("direction", sa.String(32), nullable=False),
        sa.Column("wa_message_id", sa.String(128), nullable=True),
        sa.Column("message_type", sa.String(32), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("button_id", sa.String(256), nullable=True),
        sa.Column("button_title", sa.String(64), nullable=True),
        sa.Column("question_index", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("wa_message_id", name="uq_whatsapp_messages_wa_message_id"),
    )
    op.create_index(
        "ix_whatsapp_messages_conversation_created_at",
        "whatsapp_messages",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_whatsapp_messages_conversation_created_at", table_name="whatsapp_messages"
    )
    op.drop_table("whatsapp_messages")
    op.drop_index(
        "ix_whatsapp_conversations_candidate_created_at",
        table_name="whatsapp_conversations",
    )
    op.drop_table("whatsapp_conversations")
