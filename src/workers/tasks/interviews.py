"""Celery tasks for the Calendly interview-scheduling flow (Step 6.4).

Thin sync→async bridges (``asyncio.run`` over a fresh ``SessionFactory``
session, per the worker's NullPool/event-loop model) that delegate to
:mod:`src.services.interview_scheduling_service` and own the commit. Each
publishes the presence-style :class:`~src.enums.events.AppEventType.INTERVIEW`
pointer after the commit so HR's stream refetches the application's interview
block.

* ``send_interview_invite_task`` — fires when HR moves an application into the
  ``interview`` stage. Mints the link + sends the email, then schedules the two
  follow-ups (reminder, timeout) with ``countdown`` ETAs — but only when the
  invite actually went out (an email was sent and a link exists). No
  ``autoretry_for``: minting + emailing isn't idempotent against a partial
  failure, so (as with the WhatsApp invite) a failure is logged and left for HR.

* ``handle_calendly_event_task`` — applies one inbound ``invitee.*`` webhook.
  The service is idempotent (redelivered webhooks no-op), so re-runs are safe.

* ``send_interview_reminder_task`` / ``flag_interview_timeout_task`` — the
  ETA-delayed follow-ups; both self-skip via the service if the candidate has
  since booked.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from src.config import settings
from src.db.models.application import Application
from src.db.session import SessionFactory
from src.enums.events import AppEventType
from src.enums.interviews import InterviewBookingState
from src.integrations.calendly_client import (
    EVENT_INVITEE_CANCELED,
    EVENT_INVITEE_CREATED,
    CalendlyInviteeEvent,
)
from src.schemas.events import AppEvent
from src.services.events_service import publish_app_event
from src.services.interview_scheduling_service import (
    begin_interview_invite,
    flag_interview_timeout,
    record_booking,
    record_cancellation,
    send_interview_reminder,
)
from src.workers.celery_app import SLOW_QUEUE, celery_app


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def _publish_interview_event(application_id: uuid.UUID, job_id: uuid.UUID | None) -> None:
    await publish_app_event(
        AppEvent(
            event=AppEventType.INTERVIEW,
            application_id=application_id,
            job_id=job_id,
        )
    )


# --- Invite -----------------------------------------------------------------


async def _run_begin_invite(application_id: uuid.UUID) -> bool:
    """Run the invite; return whether the reminder/timeout follow-ups should be scheduled."""
    async with SessionFactory() as session:
        booking = await begin_interview_invite(session, application_id=application_id)
        job_id: uuid.UUID | None = None
        schedule_followups = False
        if booking is not None:
            application = await session.get(Application, application_id)
            job_id = application.job_id if application is not None else None
            # Only chase a real, link-backed invite — not the no-email failure row.
            schedule_followups = (
                booking.last_error is None
                and booking.state is InterviewBookingState.INVITED
                and booking.scheduling_url is not None
            )
        await session.commit()
        if booking is not None:
            await _publish_interview_event(application_id, job_id)
    return schedule_followups


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.send_interview_invite",
    queue=SLOW_QUEUE,
)
def send_interview_invite_task(application_id: str) -> None:
    """Mint the Calendly link, email it, and schedule the follow-ups."""
    should_schedule = asyncio.run(_run_begin_invite(uuid.UUID(application_id)))
    if should_schedule:
        send_interview_reminder_task.apply_async(
            args=[application_id], countdown=settings.interview_reminder_hours * 3600
        )
        flag_interview_timeout_task.apply_async(
            args=[application_id], countdown=settings.interview_reply_timeout_hours * 3600
        )


# --- Inbound webhook --------------------------------------------------------


async def _run_handle_event(event: CalendlyInviteeEvent) -> None:
    async with SessionFactory() as session:
        if event.kind == EVENT_INVITEE_CREATED:
            booking = await record_booking(session, event=event)
        elif event.kind == EVENT_INVITEE_CANCELED:
            booking = await record_cancellation(session, event=event)
        else:
            booking = None

        application_id: uuid.UUID | None = None
        job_id: uuid.UUID | None = None
        if booking is not None:
            application_id = booking.application_id
            application = await session.get(Application, application_id)
            job_id = application.job_id if application is not None else None
        await session.commit()
        if application_id is not None:
            await _publish_interview_event(application_id, job_id)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.handle_calendly_event",
    queue=SLOW_QUEUE,
)
def handle_calendly_event_task(
    kind: str,
    tracking_token: str | None = None,
    invitee_uri: str | None = None,
    event_uri: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    invitee_email: str | None = None,
    invitee_timezone: str | None = None,
    location_type: str | None = None,
    join_url: str | None = None,
    location_text: str | None = None,
    rescheduled: bool = False,
    cancel_reason: str | None = None,
    canceled_by: str | None = None,
) -> None:
    """Apply one parsed Calendly ``invitee.*`` webhook to its booking.

    Receives the decoded :class:`CalendlyInviteeEvent` fields as primitives
    (Celery serializes JSON, not dataclasses / datetimes) and reconstructs the
    value object for the service.
    """
    event = CalendlyInviteeEvent(
        kind=kind,
        tracking_token=tracking_token,
        invitee_uri=invitee_uri,
        event_uri=event_uri,
        start_time=_parse_iso(start_time),
        end_time=_parse_iso(end_time),
        invitee_email=invitee_email,
        invitee_timezone=invitee_timezone,
        location_type=location_type,
        join_url=join_url,
        location_text=location_text,
        rescheduled=rescheduled,
        cancel_reason=cancel_reason,
        canceled_by=canceled_by,
    )
    asyncio.run(_run_handle_event(event))


# --- Follow-ups -------------------------------------------------------------


async def _run_reminder(application_id: uuid.UUID) -> None:
    async with SessionFactory() as session:
        await send_interview_reminder(session, application_id=application_id)
        await session.commit()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.send_interview_reminder",
    queue=SLOW_QUEUE,
)
def send_interview_reminder_task(application_id: str) -> None:
    """Send the single booking reminder (self-skips if already booked)."""
    asyncio.run(_run_reminder(uuid.UUID(application_id)))


async def _run_timeout(application_id: uuid.UUID) -> None:
    async with SessionFactory() as session:
        booking = await flag_interview_timeout(session, application_id=application_id)
        job_id: uuid.UUID | None = None
        if booking is not None:
            application = await session.get(Application, application_id)
            job_id = application.job_id if application is not None else None
        await session.commit()
        if booking is not None:
            await _publish_interview_event(application_id, job_id)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.flag_interview_timeout",
    queue=SLOW_QUEUE,
)
def flag_interview_timeout_task(application_id: str) -> None:
    """Flag a still-unbooked invite at the timeout so HR is alerted."""
    asyncio.run(_run_timeout(uuid.UUID(application_id)))
