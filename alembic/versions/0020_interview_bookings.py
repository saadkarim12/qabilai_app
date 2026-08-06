"""interview_bookings table.

Backs the Step 6.4 Calendly interview-scheduling flow: one row per
application's invite → booking lifecycle, created when HR moves the application
into the ``interview`` stage. Unique on ``application_id`` (one booking per
application, idempotent invite) and on ``booking_token`` (the opaque value
embedded in the single-use Calendly link's ``utm_content`` that the inbound
webhook maps back to the application).

Revision ID: 0020_interview_bookings
Revises: 0019_application_archive
Create Date: 2026-06-16
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_interview_bookings"
down_revision: str | None = "0019_application_archive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interview_bookings",
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
                name="fk_interview_bookings_application",
            ),
            nullable=False,
        ),
        sa.Column("booking_token", sa.String(64), nullable=False),
        sa.Column(
            "state",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'invited'"),
        ),
        sa.Column("scheduling_url", sa.Text(), nullable=True),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calendly_event_uri", sa.String(255), nullable=True),
        sa.Column("calendly_invitee_uri", sa.String(255), nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(255), nullable=True),
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
        sa.UniqueConstraint("application_id", name="uq_interview_bookings_application"),
        sa.UniqueConstraint("booking_token", name="uq_interview_bookings_token"),
    )


def downgrade() -> None:
    op.drop_table("interview_bookings")
