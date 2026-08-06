"""interview_bookings: meeting, invitee, reschedule + cancellation detail.

Adds the richer booking fields surfaced on the HR application-detail view once
a candidate books via Calendly: the meeting medium + join link, who booked
(email + timezone), reschedule history (prior slot + count), and the
cancellation reason / canceler role. All nullable (populated by the
``invitee.*`` webhook); ``reschedule_count`` defaults to 0.

Revision ID: 0021_interview_booking_details
Revises: 0020_interview_bookings
Create Date: 2026-06-17
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_interview_booking_details"
down_revision: str | None = "0020_interview_bookings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "interview_bookings", sa.Column("location_type", sa.String(length=64), nullable=True)
    )
    op.add_column("interview_bookings", sa.Column("join_url", sa.Text(), nullable=True))
    op.add_column("interview_bookings", sa.Column("location_text", sa.Text(), nullable=True))
    op.add_column(
        "interview_bookings", sa.Column("invitee_email", sa.String(length=320), nullable=True)
    )
    op.add_column(
        "interview_bookings", sa.Column("invitee_timezone", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "interview_bookings",
        sa.Column("previous_start_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "interview_bookings", sa.Column("rescheduled_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "interview_bookings",
        sa.Column("reschedule_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "interview_bookings", sa.Column("canceled_by", sa.String(length=64), nullable=True)
    )
    op.add_column("interview_bookings", sa.Column("cancel_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("interview_bookings", "cancel_reason")
    op.drop_column("interview_bookings", "canceled_by")
    op.drop_column("interview_bookings", "reschedule_count")
    op.drop_column("interview_bookings", "rescheduled_at")
    op.drop_column("interview_bookings", "previous_start_at")
    op.drop_column("interview_bookings", "invitee_timezone")
    op.drop_column("interview_bookings", "invitee_email")
    op.drop_column("interview_bookings", "location_text")
    op.drop_column("interview_bookings", "join_url")
    op.drop_column("interview_bookings", "location_type")
