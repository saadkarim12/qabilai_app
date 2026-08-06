"""Integration tests for the interview-scheduling service (Step 6.4).

Exercises :mod:`src.services.interview_scheduling_service` against a real DB
session, with the two external side effects — minting a Calendly link and
sending the SMTP email — stubbed so nothing leaves the process.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.application import Application
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.job import Job
from src.db.models.user import User
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.interviews import InterviewBookingState
from src.enums.jobs import EmploymentType, JobStatus, WorkMode
from src.enums.roles import UserRole
from src.integrations import calendly_client
from src.integrations.calendly_client import CalendlyInviteeEvent
from src.services import email_service
from src.services import interview_scheduling_service as svc
from src.services.application_service import get_application_detail
from src.utils.password import hash_password

EVENT_TYPE_URI = "https://api.calendly.com/event_types/EVT"


# --- Seed helpers -----------------------------------------------------------


async def _seed_application(
    db_session: AsyncSession,
    *,
    email: str | None = "candidate@example.com",
    stage: ApplicationStage = ApplicationStage.INTERVIEW,
    status: ApplicationStatus = ApplicationStatus.ACTIVE,
) -> Application:
    user = User(
        email=f"hr-{uuid.uuid4().hex[:8]}@kabil.dev",
        password_hash=hash_password("hunter2-correct"),
        full_name="HR Person",
        role=UserRole.ADMIN,
    )
    db_session.add(user)
    await db_session.flush()

    job = Job(
        created_by=user.id,
        title="Senior Backend Engineer",
        hiring_company="Kabil Test Co",
        country="AE",
        city="Dubai",
        employment_type=EmploymentType.PERMANENT,
        work_mode=WorkMode.HYBRID,
        currency="AED",
        min_experience_years=3,
        job_description="Build stuff with Python.",
        status=JobStatus.OPEN,
        public_slug=uuid.uuid4().hex[:16],
    )
    db_session.add(job)
    await db_session.flush()

    candidate = Candidate(
        email=email,
        phone_e164=f"+9715{uuid.uuid4().hex[:7]}",
        full_name="Ada Lovelace",
    )
    db_session.add(candidate)
    await db_session.flush()

    cv = CvDocument(
        candidate_id=candidate.id,
        blob_url="https://blob.invalid/test.pdf",
        blob_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        is_current=True,
    )
    db_session.add(cv)
    await db_session.flush()

    application = Application(
        job_id=job.id,
        candidate_id=candidate.id,
        cv_document_id=cv.id,
        stage=stage,
        status=status,
        consent_context={"ip": "127.0.0.1", "user_agent": "pytest"},
        consented_at=datetime.now(UTC),
    )
    db_session.add(application)
    await db_session.flush()
    return application


@pytest.fixture
def stub_side_effects(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Stub the Calendly mint + SMTP send; record their calls."""
    calls: dict[str, list[str]] = {"minted": [], "sent": []}

    async def fake_mint(event_type_uri: str) -> str:
        calls["minted"].append(event_type_uri)
        return "https://calendly.com/d/abc-def"

    async def fake_send(*, to: str, rendered: object, category: str) -> None:
        calls["sent"].append(category)

    monkeypatch.setattr(calendly_client, "create_single_use_link", fake_mint)
    monkeypatch.setattr(email_service, "send_rendered_email", fake_send)
    monkeypatch.setattr(svc.settings, "calendly_event_type_uri", EVENT_TYPE_URI)
    return calls


# --- begin_interview_invite -------------------------------------------------


async def test_invite_happy_path(
    migrations_applied: None,
    db_session: AsyncSession,
    stub_side_effects: dict[str, list[str]],
) -> None:
    application = await _seed_application(db_session)

    booking = await svc.begin_interview_invite(db_session, application_id=application.id)

    assert booking is not None
    assert booking.state is InterviewBookingState.INVITED
    assert booking.last_error is None
    assert booking.scheduling_url is not None
    # The opaque tracking token is embedded in the emailed link.
    assert f"utm_content={booking.booking_token}" in booking.scheduling_url
    assert stub_side_effects["minted"] == [EVENT_TYPE_URI]
    assert stub_side_effects["sent"] == ["interview_invite"]


async def test_invite_is_idempotent(
    migrations_applied: None,
    db_session: AsyncSession,
    stub_side_effects: dict[str, list[str]],
) -> None:
    application = await _seed_application(db_session)

    first = await svc.begin_interview_invite(db_session, application_id=application.id)
    second = await svc.begin_interview_invite(db_session, application_id=application.id)

    assert first is not None and second is not None
    assert first.id == second.id
    # No second mint or email on the duplicate enqueue.
    assert len(stub_side_effects["minted"]) == 1
    assert len(stub_side_effects["sent"]) == 1


async def test_invite_without_email_records_error(
    migrations_applied: None,
    db_session: AsyncSession,
    stub_side_effects: dict[str, list[str]],
) -> None:
    application = await _seed_application(db_session, email=None)

    booking = await svc.begin_interview_invite(db_session, application_id=application.id)

    assert booking is not None
    assert booking.last_error == svc.ERROR_EMAIL_UNAVAILABLE
    assert booking.scheduling_url is None
    # Neither external call happens when there's no address to send to.
    assert stub_side_effects["minted"] == []
    assert stub_side_effects["sent"] == []


async def test_invite_skips_when_not_interview_stage(
    migrations_applied: None,
    db_session: AsyncSession,
    stub_side_effects: dict[str, list[str]],
) -> None:
    application = await _seed_application(db_session, stage=ApplicationStage.WHATSAPP)

    booking = await svc.begin_interview_invite(db_session, application_id=application.id)

    assert booking is None
    assert stub_side_effects["minted"] == []


# --- webhook application -----------------------------------------------------


def _event(
    kind: str,
    token: str,
    *,
    invitee: str = "INV-1",
    start: datetime | None = None,
    end: datetime | None = None,
    join_url: str | None = "https://zoom.us/j/123",
    location_type: str | None = "zoom",
    invitee_email: str | None = "ada@example.com",
    invitee_timezone: str | None = "Asia/Karachi",
    rescheduled: bool = False,
    cancel_reason: str | None = None,
    canceled_by: str | None = None,
) -> CalendlyInviteeEvent:
    return CalendlyInviteeEvent(
        kind=kind,
        tracking_token=token,
        invitee_uri=f"https://api.calendly.com/scheduled_events/E/invitees/{invitee}",
        event_uri="https://api.calendly.com/scheduled_events/E",
        start_time=start or datetime(2026, 6, 20, 15, 0, tzinfo=UTC),
        end_time=end or datetime(2026, 6, 20, 15, 30, tzinfo=UTC),
        join_url=join_url,
        location_type=location_type,
        invitee_email=invitee_email,
        invitee_timezone=invitee_timezone,
        rescheduled=rescheduled,
        cancel_reason=cancel_reason,
        canceled_by=canceled_by,
    )


async def test_record_booking_marks_booked_and_dedupes(
    migrations_applied: None,
    db_session: AsyncSession,
    stub_side_effects: dict[str, list[str]],
) -> None:
    application = await _seed_application(db_session)
    invited = await svc.begin_interview_invite(db_session, application_id=application.id)
    assert invited is not None
    token = invited.booking_token

    booked = await svc.record_booking(db_session, event=_event("invitee.created", token))
    assert booked is not None
    assert booked.state is InterviewBookingState.BOOKED
    assert booked.scheduled_start_at == datetime(2026, 6, 20, 15, 0, tzinfo=UTC)
    assert booked.calendly_invitee_uri is not None

    # Redelivery of the same invitee is a no-op.
    again = await svc.record_booking(db_session, event=_event("invitee.created", token))
    assert again is None


async def test_record_booking_unknown_token_is_none(
    migrations_applied: None,
    db_session: AsyncSession,
    stub_side_effects: dict[str, list[str]],
) -> None:
    result = await svc.record_booking(db_session, event=_event("invitee.created", "no-such-token"))
    assert result is None


async def test_record_booking_captures_meeting_and_invitee_details(
    migrations_applied: None,
    db_session: AsyncSession,
    stub_side_effects: dict[str, list[str]],
) -> None:
    application = await _seed_application(db_session)
    invited = await svc.begin_interview_invite(db_session, application_id=application.id)
    assert invited is not None

    booked = await svc.record_booking(
        db_session, event=_event("invitee.created", invited.booking_token)
    )
    assert booked is not None
    assert booked.join_url == "https://zoom.us/j/123"
    assert booked.location_type == "zoom"
    assert booked.invitee_email == "ada@example.com"
    assert booked.invitee_timezone == "Asia/Karachi"
    assert booked.reschedule_count == 0
    assert booked.previous_start_at is None


async def test_record_booking_reschedule_tracks_previous_slot(
    migrations_applied: None,
    db_session: AsyncSession,
    stub_side_effects: dict[str, list[str]],
) -> None:
    application = await _seed_application(db_session)
    invited = await svc.begin_interview_invite(db_session, application_id=application.id)
    assert invited is not None
    token = invited.booking_token

    first_start = datetime(2026, 6, 20, 15, 0, tzinfo=UTC)
    await svc.record_booking(db_session, event=_event("invitee.created", token, invitee="INV-1"))

    # A new invitee (reschedule) with a later slot keeps the prior time + bumps.
    new_start = datetime(2026, 6, 22, 9, 0, tzinfo=UTC)
    rebooked = await svc.record_booking(
        db_session,
        event=_event(
            "invitee.created",
            token,
            invitee="INV-2",
            start=new_start,
            end=datetime(2026, 6, 22, 9, 30, tzinfo=UTC),
        ),
    )
    assert rebooked is not None
    assert rebooked.state is InterviewBookingState.BOOKED
    assert rebooked.scheduled_start_at == new_start
    assert rebooked.previous_start_at == first_start
    assert rebooked.reschedule_count == 1
    assert rebooked.rescheduled_at is not None


async def test_record_cancellation(
    migrations_applied: None,
    db_session: AsyncSession,
    stub_side_effects: dict[str, list[str]],
) -> None:
    application = await _seed_application(db_session)
    invited = await svc.begin_interview_invite(db_session, application_id=application.id)
    assert invited is not None
    token = invited.booking_token
    await svc.record_booking(db_session, event=_event("invitee.created", token))

    canceled = await svc.record_cancellation(
        db_session,
        event=_event(
            "invitee.canceled", token, cancel_reason="Schedule conflict", canceled_by="invitee"
        ),
    )
    assert canceled is not None
    assert canceled.state is InterviewBookingState.CANCELED
    assert canceled.canceled_at is not None
    assert canceled.cancel_reason == "Schedule conflict"
    assert canceled.canceled_by == "invitee"

    # Idempotent against a redelivered cancellation.
    again = await svc.record_cancellation(db_session, event=_event("invitee.canceled", token))
    assert again is None


# --- follow-ups -------------------------------------------------------------


async def test_reminder_sends_once_then_skips(
    migrations_applied: None,
    db_session: AsyncSession,
    stub_side_effects: dict[str, list[str]],
) -> None:
    application = await _seed_application(db_session)
    await svc.begin_interview_invite(db_session, application_id=application.id)
    stub_side_effects["sent"].clear()

    first = await svc.send_interview_reminder(db_session, application_id=application.id)
    assert first is not None
    assert first.reminder_sent_at is not None
    assert stub_side_effects["sent"] == ["interview_reminder"]

    # Second run is a no-op (reminder already sent).
    second = await svc.send_interview_reminder(db_session, application_id=application.id)
    assert second is None
    assert stub_side_effects["sent"] == ["interview_reminder"]


async def test_reminder_skips_after_booking(
    migrations_applied: None,
    db_session: AsyncSession,
    stub_side_effects: dict[str, list[str]],
) -> None:
    application = await _seed_application(db_session)
    invited = await svc.begin_interview_invite(db_session, application_id=application.id)
    assert invited is not None
    await svc.record_booking(db_session, event=_event("invitee.created", invited.booking_token))
    stub_side_effects["sent"].clear()

    result = await svc.send_interview_reminder(db_session, application_id=application.id)
    assert result is None
    assert stub_side_effects["sent"] == []


async def test_timeout_flags_unbooked_only(
    migrations_applied: None,
    db_session: AsyncSession,
    stub_side_effects: dict[str, list[str]],
) -> None:
    application = await _seed_application(db_session)
    invited = await svc.begin_interview_invite(db_session, application_id=application.id)
    assert invited is not None

    flagged = await svc.flag_interview_timeout(db_session, application_id=application.id)
    assert flagged is not None
    assert flagged.last_error == svc.ERROR_UNBOOKED_TIMEOUT

    # Once booked, the timeout no longer flags.
    await svc.record_booking(db_session, event=_event("invitee.created", invited.booking_token))
    again = await svc.flag_interview_timeout(db_session, application_id=application.id)
    assert again is None


# --- detail surfacing -------------------------------------------------------


async def test_detail_has_no_interview_block_before_invite(
    migrations_applied: None,
    db_session: AsyncSession,
    stub_side_effects: dict[str, list[str]],
) -> None:
    application = await _seed_application(db_session)

    detail = await get_application_detail(db_session, application.id)

    assert detail.interview is None


async def test_detail_surfaces_booking_after_invite(
    migrations_applied: None,
    db_session: AsyncSession,
    stub_side_effects: dict[str, list[str]],
) -> None:
    application = await _seed_application(db_session)
    booking = await svc.begin_interview_invite(db_session, application_id=application.id)
    assert booking is not None

    detail = await get_application_detail(db_session, application.id)

    assert detail.interview is not None
    assert detail.interview.id == booking.id
    assert detail.interview.state is InterviewBookingState.INVITED
    assert detail.interview.scheduling_url == booking.scheduling_url
