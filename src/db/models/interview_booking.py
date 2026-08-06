"""Interview booking ORM model (Step 6.4).

One :class:`InterviewBooking` row per application's interview-scheduling
lifecycle, created when HR moves the application into the ``interview`` stage.
It records the single-use Calendly link we minted, the opaque ``booking_token``
we embed in that link's ``utm_content`` (so the inbound Calendly webhook can map
a booking back to *this* application without exposing internal ids), and the
scheduled slot once the candidate books.

Unique on ``application_id`` — an application enters the interview stage once,
and the invite task is idempotent against this row. The scheduled-time and
Calendly-URI columns stay NULL until the ``invitee.created`` webhook lands.

Calendly itself emails both the candidate and the shared host the calendar
invite (.ics) on booking, so this table is our own audit/state — it holds no
message bodies. It does record a little booking PII (the invitee email +
timezone and the meeting join link) for the HR detail view; that surfaces only
through the HR-authed application detail and is never logged.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    UUID,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base
from src.enums.interviews import InterviewBookingState

BOOKING_TOKEN_MAX_LENGTH = 64  # secrets.token_urlsafe(32) → ~43 chars; headroom.
CALENDLY_URI_MAX_LENGTH = 255  # ``https://api.calendly.com/scheduled_events/<uuid>``.
LAST_ERROR_MAX_LENGTH = 255  # Short PII-free reason code (e.g. "email_unavailable").
LOCATION_TYPE_MAX_LENGTH = 64  # Calendly location discriminator (e.g. "google_conference").
EMAIL_MAX_LENGTH = 320  # RFC 5321 max addr length; matches candidate email headroom.
TIMEZONE_MAX_LENGTH = 64  # IANA tz name (e.g. "Asia/Karachi").
CANCELED_BY_MAX_LENGTH = 64  # Role ("invitee"/"host") or a short canceler name.


def _enum_column(enum_cls: type[StrEnum]) -> Enum:
    """Varchar-backed enum column that stores ``.value`` (not ``.name``)."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=32,
        create_constraint=False,
        values_callable=lambda cls: [e.value for e in cls],
    )


class InterviewBooking(Base):
    """One application's interview invite → booking state."""

    __tablename__ = "interview_bookings"
    __table_args__ = (
        UniqueConstraint("application_id", name="uq_interview_bookings_application"),
        UniqueConstraint("booking_token", name="uq_interview_bookings_token"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, server_default=text("gen_random_uuid()")
    )

    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey(
            "applications.id",
            ondelete="CASCADE",
            name="fk_interview_bookings_application",
        ),
        nullable=False,
    )

    # Opaque, single-use token embedded in the Calendly link's ``utm_content``.
    # The webhook reads it back to resolve the application — a random token
    # rather than the raw application_id, so internal ids never leave our system.
    booking_token: Mapped[str] = mapped_column(String(BOOKING_TOKEN_MAX_LENGTH), nullable=False)

    state: Mapped[InterviewBookingState] = mapped_column(
        _enum_column(InterviewBookingState),
        nullable=False,
        server_default=text(f"'{InterviewBookingState.INVITED.value}'"),
    )

    # The minted single-use Calendly scheduling URL we emailed the candidate.
    # NULL only if link generation itself failed (the row still records the
    # attempt + ``last_error``).
    scheduling_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Booked slot (set on invitee.created) -------------------------------
    scheduled_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scheduled_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Calendly resource URIs for the booked event + invitee. The invitee URI is
    # what cancellation/reschedule webhooks reference back to.
    calendly_event_uri: Mapped[str | None] = mapped_column(
        String(CALENDLY_URI_MAX_LENGTH), nullable=True
    )
    calendly_invitee_uri: Mapped[str | None] = mapped_column(
        String(CALENDLY_URI_MAX_LENGTH), nullable=True
    )

    # --- Meeting details (set on invitee.created) ---------------------------
    # The meeting medium and how to reach it: ``location_type`` is Calendly's
    # discriminator (``zoom`` / ``google_conference`` / ``physical`` / …);
    # ``join_url`` is the virtual join link; ``location_text`` is the physical
    # address or phone number for non-URL meetings. HR shows whichever applies.
    location_type: Mapped[str | None] = mapped_column(
        String(LOCATION_TYPE_MAX_LENGTH), nullable=True
    )
    join_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Who booked, for the HR view (the candidate row holds our own copy; this is
    # what Calendly recorded). ``invitee_timezone`` lets the FE localize the slot.
    invitee_email: Mapped[str | None] = mapped_column(String(EMAIL_MAX_LENGTH), nullable=True)
    invitee_timezone: Mapped[str | None] = mapped_column(String(TIMEZONE_MAX_LENGTH), nullable=True)

    # --- Reschedule tracking ------------------------------------------------
    # On a reschedule the slot is moved (state stays BOOKED); we keep the prior
    # start time + a count + when it last happened so HR sees the history.
    previous_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rescheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reschedule_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    # --- Cancellation detail (set on invitee.canceled) ----------------------
    # ``canceled_by`` is the role (``invitee`` / ``host``); ``cancel_reason`` is
    # the free-text reason Calendly collected, so HR sees why a slot fell through.
    canceled_by: Mapped[str | None] = mapped_column(String(CANCELED_BY_MAX_LENGTH), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Follow-up bookkeeping ---------------------------------------------
    # Set when the single reminder is sent; its presence makes the reminder
    # task idempotent (it no-ops if already set).
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Short PII-free reason code for the last failure (invite send, link mint),
    # surfaced to HR on the application detail so a stuck invite is visible.
    last_error: Mapped[str | None] = mapped_column(String(LAST_ERROR_MAX_LENGTH), nullable=True)

    # --- Timestamps ---------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )

    def __repr__(self) -> str:
        return (
            f"InterviewBooking(id={self.id!r}, application_id={self.application_id!r}, "
            f"state={self.state!r})"
        )
