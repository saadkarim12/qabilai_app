"""Interview scheduling — state machine + persistence (Step 6.4).

Owns the Calendly-driven interview booking flow that fires when HR moves an
application into the ``interview`` stage:

* :func:`begin_interview_invite` — mint a single-use Calendly link, email it to
  the candidate, and open the :class:`InterviewBooking` row. Idempotent against
  re-entry (the unique ``application_id`` row guards a double-invite).
* :func:`send_interview_reminder` — send the single follow-up reminder if the
  candidate still hasn't booked.
* :func:`flag_interview_timeout` — mark a still-unbooked invite at the timeout
  so HR is alerted; the application stage is left untouched.
* :func:`record_booking` / :func:`record_cancellation` — apply an inbound
  Calendly ``invitee.created`` / ``invitee.canceled`` webhook, matched back to
  the booking by the opaque ``utm_content`` token.

Each function self-skips (logs and returns ``None``) when its precondition
isn't met, mirroring the WhatsApp service. Calendly emails both the candidate
and the shared host the calendar invite (.ics) on booking, so nothing here
sends a confirmation; this module only records state and renders *our* invite /
reminder emails. Candidate name/email never reach the logs.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime
from urllib.parse import urlencode

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models.application import Application
from src.db.models.candidate import Candidate
from src.db.models.interview_booking import InterviewBooking
from src.db.models.job import Job
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.interviews import InterviewBookingState
from src.integrations import calendly_client
from src.integrations.calendly_client import CalendlyInviteeEvent
from src.services import email_service

logger = logging.getLogger(__name__)

# Tracking-param key on the Calendly link; Calendly echoes it back under
# ``payload.tracking.utm_content`` on the webhook, which is how we map a booking
# to its application without exposing internal ids.
TRACKING_PARAM: str = "utm_content"

# PII-free log/category tags for the two emails this service sends.
CATEGORY_INVITE: str = "interview_invite"
CATEGORY_REMINDER: str = "interview_reminder"

# ``last_error`` reason codes (short, PII-free) surfaced to HR on the detail.
ERROR_EMAIL_UNAVAILABLE: str = "email_unavailable"
ERROR_UNBOOKED_TIMEOUT: str = "unbooked_timeout"

# Number of random bytes for the booking token (~43 url-safe chars).
_TOKEN_BYTES: int = 32


def _now() -> datetime:
    return datetime.now(UTC)


def _build_tracked_url(booking_url: str, token: str) -> str:
    """Append the ``utm_content`` tracking token to a Calendly booking URL.

    Single-use links carry no query string, but we join defensively so an
    existing query wouldn't be clobbered.
    """
    separator = "&" if "?" in booking_url else "?"
    return f"{booking_url}{separator}{urlencode({TRACKING_PARAM: token})}"


def _email_context(candidate: Candidate, job: Job, booking_url: str) -> dict[str, str]:
    return {
        "candidate_name": candidate.full_name,
        "job_title": job.title,
        "company_name": job.hiring_company,
        "booking_url": booking_url,
    }


async def _load_active_interview(
    session: AsyncSession, application_id: uuid.UUID, *, log_prefix: str
) -> tuple[Application, Candidate, Job] | None:
    """Load (application, candidate, job) iff the app is an active interview.

    Self-skips (returns ``None``) when the application is gone, not in the
    ``interview`` stage, not ``active``, or its candidate/job is missing.
    """
    application = await session.get(Application, application_id)
    if application is None:
        logger.warning(
            f"{log_prefix}.application_missing", extra={"application_id": str(application_id)}
        )
        return None
    if application.stage is not ApplicationStage.INTERVIEW:
        logger.info(
            f"{log_prefix}.skipped_stage",
            extra={"application_id": str(application_id), "stage": application.stage.value},
        )
        return None
    if application.status is not ApplicationStatus.ACTIVE:
        logger.info(
            f"{log_prefix}.skipped_status",
            extra={"application_id": str(application_id), "status": application.status.value},
        )
        return None

    candidate = await session.get(Candidate, application.candidate_id)
    job = await session.get(Job, application.job_id)
    if candidate is None or job is None:
        logger.warning(
            f"{log_prefix}.candidate_or_job_missing", extra={"application_id": str(application_id)}
        )
        return None
    return application, candidate, job


async def _booking_for_application(
    session: AsyncSession, application_id: uuid.UUID
) -> InterviewBooking | None:
    return (
        await session.execute(
            sa.select(InterviewBooking)
            .where(InterviewBooking.application_id == application_id)
            .with_for_update()
        )
    ).scalar_one_or_none()


async def _booking_for_token(session: AsyncSession, token: str) -> InterviewBooking | None:
    return (
        await session.execute(
            sa.select(InterviewBooking)
            .where(InterviewBooking.booking_token == token)
            .with_for_update()
        )
    ).scalar_one_or_none()


async def begin_interview_invite(
    session: AsyncSession, *, application_id: uuid.UUID
) -> InterviewBooking | None:
    """Mint a Calendly link, email it, and open the booking row.

    Returns the :class:`InterviewBooking` (so the task can publish an event and
    schedule follow-ups), or ``None`` when the call self-skips. A booking row
    already existing means we've invited before — return it unchanged so a
    duplicate enqueue can't double-send.

    When the candidate has no email the row is still created with
    ``last_error=email_unavailable`` (and no link) so HR sees the stuck invite;
    no exception is raised for that expected condition. Calendly/SMTP transport
    errors propagate to the caller (the task logs and leaves it for HR), so no
    partial row is written for a transient failure.
    """
    loaded = await _load_active_interview(session, application_id, log_prefix="interview.invite")
    if loaded is None:
        return None
    _, candidate, job = loaded

    existing = await _booking_for_application(session, application_id)
    if existing is not None:
        logger.info(
            "interview.invite.already_invited", extra={"application_id": str(application_id)}
        )
        return existing

    token = secrets.token_urlsafe(_TOKEN_BYTES)

    # No email → record the attempt + reason; HR chases manually. No Calendly call.
    if not candidate.email:
        booking = InterviewBooking(
            application_id=application_id,
            booking_token=token,
            state=InterviewBookingState.INVITED,
            last_error=ERROR_EMAIL_UNAVAILABLE,
        )
        session.add(booking)
        await session.flush()
        logger.warning("interview.invite.no_email", extra={"application_id": str(application_id)})
        return booking

    # Side effects first (mint + send): a transient failure here propagates and
    # leaves nothing persisted, matching the WhatsApp invite's all-or-nothing.
    event_type_uri = _require_event_type_uri()
    booking_url = await calendly_client.create_single_use_link(event_type_uri)
    tracked_url = _build_tracked_url(booking_url, token)

    rendered = email_service.render_email(
        CATEGORY_INVITE, _email_context(candidate, job, tracked_url)
    )
    await email_service.send_rendered_email(
        to=candidate.email, rendered=rendered, category=CATEGORY_INVITE
    )

    booking = InterviewBooking(
        application_id=application_id,
        booking_token=token,
        state=InterviewBookingState.INVITED,
        scheduling_url=tracked_url,
    )
    session.add(booking)
    await session.flush()
    logger.info("interview.invite.sent", extra={"application_id": str(application_id)})
    return booking


def _require_event_type_uri() -> str:
    uri = settings.calendly_event_type_uri
    if not uri:
        raise calendly_client.CalendlyConfigError(
            "CALENDLY_EVENT_TYPE_URI is not configured; cannot mint a scheduling link"
        )
    return uri


async def send_interview_reminder(
    session: AsyncSession, *, application_id: uuid.UUID
) -> InterviewBooking | None:
    """Send the single booking reminder if the invite is still unbooked.

    Self-skips when there's no booking, it isn't still ``invited``, a reminder
    already went out, or the original invite never produced a link (e.g. the
    no-email case). Returns the booking on send, else ``None``.
    """
    booking = await _booking_for_application(session, application_id)
    if booking is None or booking.state is not InterviewBookingState.INVITED:
        return None
    if booking.reminder_sent_at is not None or not booking.scheduling_url:
        return None

    loaded = await _load_active_interview(session, application_id, log_prefix="interview.reminder")
    if loaded is None:
        return None
    _, candidate, job = loaded
    if not candidate.email:
        return None

    rendered = email_service.render_email(
        CATEGORY_REMINDER, _email_context(candidate, job, booking.scheduling_url)
    )
    await email_service.send_rendered_email(
        to=candidate.email, rendered=rendered, category=CATEGORY_REMINDER
    )
    booking.reminder_sent_at = _now()
    await session.flush()
    logger.info("interview.reminder.sent", extra={"application_id": str(application_id)})
    return booking


async def flag_interview_timeout(
    session: AsyncSession, *, application_id: uuid.UUID
) -> InterviewBooking | None:
    """Flag a still-unbooked invite at the timeout for HR attention.

    Returns the booking (so the task can publish an event) only when it's still
    ``invited`` at the deadline; ``None`` if it was already booked/canceled or
    has no booking. Leaves the application stage untouched — HR decides.
    """
    booking = await _booking_for_application(session, application_id)
    if booking is None or booking.state is not InterviewBookingState.INVITED:
        return None
    booking.last_error = ERROR_UNBOOKED_TIMEOUT
    await session.flush()
    logger.info("interview.timeout.flagged", extra={"application_id": str(application_id)})
    return booking


async def record_booking(
    session: AsyncSession, *, event: CalendlyInviteeEvent
) -> InterviewBooking | None:
    """Apply an ``invitee.created`` webhook: mark the booking ``booked``.

    Matched to the booking by ``event.tracking_token``. Idempotent — a
    redelivered webhook for the same invitee is a no-op (returns ``None``).
    Returns the booking on a real change so the task can nudge HR.
    """
    if not event.tracking_token:
        logger.warning("interview.booking.no_token")
        return None
    booking = await _booking_for_token(session, event.tracking_token)
    if booking is None:
        logger.warning("interview.booking.unknown_token")
        return None

    # Redelivery of the same booking → no-op.
    if (
        booking.state is InterviewBookingState.BOOKED
        and booking.calendly_invitee_uri == event.invitee_uri
    ):
        return None

    # A new slot on an already-booked row (a reschedule, or Calendly's
    # ``rescheduled`` flag) → keep the prior time + bump the counter so HR sees
    # the change history rather than a silent overwrite.
    was_booked = (
        booking.state is InterviewBookingState.BOOKED and booking.scheduled_start_at is not None
    )
    if was_booked or event.rescheduled:
        booking.previous_start_at = booking.scheduled_start_at
        booking.reschedule_count += 1
        booking.rescheduled_at = _now()

    booking.state = InterviewBookingState.BOOKED
    booking.scheduled_start_at = event.start_time
    booking.scheduled_end_at = event.end_time
    booking.calendly_event_uri = event.event_uri
    booking.calendly_invitee_uri = event.invitee_uri
    booking.invitee_email = event.invitee_email
    booking.invitee_timezone = event.invitee_timezone
    booking.location_type = event.location_type
    booking.join_url = event.join_url
    booking.location_text = event.location_text
    # Clear any prior cancellation — this row is booked again.
    booking.canceled_at = None
    booking.cancel_reason = None
    booking.canceled_by = None
    booking.last_error = None
    await session.flush()
    logger.info("interview.booking.recorded", extra={"application_id": str(booking.application_id)})
    return booking


async def record_cancellation(
    session: AsyncSession, *, event: CalendlyInviteeEvent
) -> InterviewBooking | None:
    """Apply an ``invitee.canceled`` webhook: mark the booking ``canceled``.

    Matched by ``event.tracking_token``. Idempotent against a redelivered
    cancellation. Returns the booking on a real change. The application stage is
    intentionally left where it is for HR to decide the next move.
    """
    if not event.tracking_token:
        logger.warning("interview.cancellation.no_token")
        return None
    booking = await _booking_for_token(session, event.tracking_token)
    if booking is None:
        logger.warning("interview.cancellation.unknown_token")
        return None
    if booking.state is InterviewBookingState.CANCELED:
        return None

    booking.state = InterviewBookingState.CANCELED
    booking.canceled_at = _now()
    booking.cancel_reason = event.cancel_reason
    booking.canceled_by = event.canceled_by
    await session.flush()
    logger.info(
        "interview.cancellation.recorded", extra={"application_id": str(booking.application_id)}
    )
    return booking
