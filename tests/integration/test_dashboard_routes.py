"""Integration tests for the ``GET /dashboard`` summary endpoint.

Covers the contract the FE relies on:

* Auth is required.
* An empty workspace returns zeroed counts with every enum key present.
* Seeded jobs / applications / candidates / talent-pool entries roll up
  into the right totals and per-stage / per-status breakdowns.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.application import Application
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.interview_booking import InterviewBooking
from src.db.models.job import Job
from src.db.models.talent_pool_entry import TalentPoolEntry
from src.db.models.user import User
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.dashboard import JobHealth, PipelineBucket
from src.enums.interviews import InterviewBookingState
from src.enums.jobs import EmploymentType, JobStatus, WorkMode
from src.enums.roles import UserRole
from src.enums.thresholds import INTERVIEW_PENDING_FEEDBACK_DAYS
from src.utils.password import hash_password

pytestmark = pytest.mark.integration


# --- Seed / auth helpers ----------------------------------------------------


async def _seed_user(db_session: AsyncSession) -> tuple[User, str]:
    password = "hunter2-correct"
    user = User(
        email=f"hr-{uuid.uuid4().hex[:8]}@kabil.dev",
        password_hash=hash_password(password),
        full_name="HR Person",
        role=UserRole.ADMIN,
    )
    db_session.add(user)
    await db_session.flush()
    return user, password


async def _login(client: httpx.AsyncClient, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


async def _seed_job(
    db_session: AsyncSession,
    *,
    created_by: uuid.UUID,
    status: JobStatus = JobStatus.OPEN,
    title: str = "Senior Backend Engineer",
    created_at: datetime | None = None,
    closed_at: datetime | None = None,
) -> Job:
    job = Job(
        created_by=created_by,
        title=title,
        hiring_company="Kabil Test Co",
        country="AE",
        city="Dubai",
        employment_type=EmploymentType.PERMANENT,
        work_mode=WorkMode.HYBRID,
        currency="AED",
        min_experience_years=3,
        job_description="Build stuff with Python.",
        status=status,
        public_slug=uuid.uuid4().hex[:16],
        closed_at=closed_at,
    )
    if created_at is not None:
        job.created_at = created_at
    db_session.add(job)
    await db_session.flush()
    return job


async def _seed_candidate(db_session: AsyncSession) -> Candidate:
    cand = Candidate(
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        phone_e164=f"+9715{uuid.uuid4().hex[:7]}",
        full_name="Test Candidate",
    )
    db_session.add(cand)
    await db_session.flush()
    return cand


async def _seed_cv_doc(db_session: AsyncSession, *, candidate_id: uuid.UUID) -> CvDocument:
    cv = CvDocument(
        candidate_id=candidate_id,
        blob_url="https://blob.invalid/test.pdf",
        blob_sha256=uuid.uuid4().hex + uuid.uuid4().hex,  # 64-char hex
        is_current=True,
    )
    db_session.add(cv)
    await db_session.flush()
    return cv


async def _seed_application(
    db_session: AsyncSession,
    *,
    job_id: uuid.UUID,
    candidate_id: uuid.UUID,
    cv_document_id: uuid.UUID,
    stage: ApplicationStage = ApplicationStage.VECTOR_SCREEN,
    status: ApplicationStatus = ApplicationStatus.ACTIVE,
    stage_updated_at: datetime | None = None,
) -> Application:
    app = Application(
        job_id=job_id,
        candidate_id=candidate_id,
        cv_document_id=cv_document_id,
        stage=stage,
        status=status,
        consent_context={"ip": "127.0.0.1", "user_agent": "pytest"},
        consented_at=datetime.now(UTC),
    )
    if stage_updated_at is not None:
        app.stage_updated_at = stage_updated_at
    db_session.add(app)
    await db_session.flush()
    return app


async def _seed_full_application(
    db_session: AsyncSession,
    *,
    job_id: uuid.UUID,
    stage: ApplicationStage = ApplicationStage.VECTOR_SCREEN,
    status: ApplicationStatus = ApplicationStatus.ACTIVE,
    stage_updated_at: datetime | None = None,
) -> Application:
    """Seed a fresh candidate + CV + application on ``job_id`` in one call."""
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    return await _seed_application(
        db_session,
        job_id=job_id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
        stage=stage,
        status=status,
        stage_updated_at=stage_updated_at,
    )


async def _seed_booking(
    db_session: AsyncSession,
    *,
    application_id: uuid.UUID,
    scheduled_start_at: datetime,
    state: InterviewBookingState = InterviewBookingState.BOOKED,
    join_url: str | None = "https://zoom.invalid/j/123",
) -> InterviewBooking:
    booking = InterviewBooking(
        application_id=application_id,
        booking_token=uuid.uuid4().hex,
        state=state,
        scheduled_start_at=scheduled_start_at,
        scheduled_end_at=scheduled_start_at + timedelta(minutes=45),
        location_type="zoom",
        join_url=join_url,
        invitee_timezone="Asia/Karachi",
    )
    db_session.add(booking)
    await db_session.flush()
    return booking


# --- Auth gate --------------------------------------------------------------


async def test_dashboard_requires_auth(migrations_applied: None, client: httpx.AsyncClient) -> None:
    resp = await client.get("/dashboard")
    assert resp.status_code == 401


# --- Shape ------------------------------------------------------------------


async def test_dashboard_keys_always_present(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    """Every enum value is a key even when the workspace has no such rows."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    resp = await client.get("/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert set(body["jobs"]["by_status"]) == {s.value for s in JobStatus}
    assert set(body["applications"]["by_stage"]) == {s.value for s in ApplicationStage}
    assert set(body["applications"]["by_status"]) == {s.value for s in ApplicationStatus}
    # Counts are non-negative ints regardless of the rows other tests left behind.
    assert all(v >= 0 for v in body["jobs"]["by_status"].values())


# --- Roll-up ----------------------------------------------------------------


async def test_dashboard_counts_reflect_seeded_rows(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    """Deltas (not absolutes) are asserted so a shared dev DB stays safe."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    before = (await client.get("/dashboard", headers={"Authorization": f"Bearer {token}"})).json()

    open_job = await _seed_job(db_session, created_by=user.id, status=JobStatus.OPEN)
    await _seed_job(db_session, created_by=user.id, status=JobStatus.DRAFT)

    # Two applications on the open job: one active@vector_screen, one
    # rejected@hard_filter.
    cand_a = await _seed_candidate(db_session)
    cv_a = await _seed_cv_doc(db_session, candidate_id=cand_a.id)
    await _seed_application(
        db_session,
        job_id=open_job.id,
        candidate_id=cand_a.id,
        cv_document_id=cv_a.id,
        stage=ApplicationStage.VECTOR_SCREEN,
        status=ApplicationStatus.ACTIVE,
    )

    cand_b = await _seed_candidate(db_session)
    cv_b = await _seed_cv_doc(db_session, candidate_id=cand_b.id)
    await _seed_application(
        db_session,
        job_id=open_job.id,
        candidate_id=cand_b.id,
        cv_document_id=cv_b.id,
        stage=ApplicationStage.HARD_FILTER,
        status=ApplicationStatus.REJECTED,
    )

    db_session.add(
        TalentPoolEntry(
            candidate_id=cand_a.id,
            expires_at=datetime.now(UTC) + timedelta(days=30),
            is_active=True,
        )
    )
    await db_session.flush()

    after = (await client.get("/dashboard", headers={"Authorization": f"Bearer {token}"})).json()

    assert after["jobs"]["total"] - before["jobs"]["total"] == 2
    assert after["jobs"]["by_status"]["open"] - before["jobs"]["by_status"]["open"] == 1
    assert after["jobs"]["by_status"]["draft"] - before["jobs"]["by_status"]["draft"] == 1

    assert after["applications"]["total"] - before["applications"]["total"] == 2
    assert (
        after["applications"]["by_stage"]["vector_screen"]
        - before["applications"]["by_stage"]["vector_screen"]
        == 1
    )
    assert (
        after["applications"]["by_stage"]["hard_filter"]
        - before["applications"]["by_stage"]["hard_filter"]
        == 1
    )
    assert (
        after["applications"]["by_status"]["active"] - before["applications"]["by_status"]["active"]
        == 1
    )
    assert (
        after["applications"]["by_status"]["rejected"]
        - before["applications"]["by_status"]["rejected"]
        == 1
    )

    assert after["candidates"]["total"] - before["candidates"]["total"] == 2
    assert after["talent_pool"]["active"] - before["talent_pool"]["active"] == 1


# --- Performance ------------------------------------------------------------


async def test_performance_requires_auth(
    migrations_applied: None, client: httpx.AsyncClient
) -> None:
    resp = await client.get("/dashboard/performance")
    assert resp.status_code == 401


async def test_performance_health_and_exclusions(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    """Health is age-based on open jobs; drafts excluded; counts exclude archived."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    now = datetime.now(UTC)

    # Old OPEN job: 60 calendar days is well past 20 *working* days no matter
    # what weekday "now" is, so this is unambiguously UNHEALTHY. A done candidate
    # no longer affects health (age-only now) but still drives the shortlist count.
    unhealthy = await _seed_job(
        db_session,
        created_by=user.id,
        status=JobStatus.OPEN,
        created_at=now - timedelta(days=60),
    )
    await _seed_full_application(db_session, job_id=unhealthy.id, stage=ApplicationStage.DONE)
    await _seed_full_application(db_session, job_id=unhealthy.id, stage=ApplicationStage.WHATSAPP)
    # An archived stint must not inflate candidate / shortlist counts.
    await _seed_full_application(
        db_session,
        job_id=unhealthy.id,
        stage=ApplicationStage.DONE,
        status=ApplicationStatus.ARCHIVED,
    )

    # Young OPEN job (2 days spans at most 2 working days) → HEALTHY.
    healthy = await _seed_job(
        db_session,
        created_by=user.id,
        status=JobStatus.OPEN,
        created_at=now - timedelta(days=2),
    )

    # Old but CLOSED job → HEALTHY: only OPEN jobs degrade, regardless of age.
    closed = await _seed_job(
        db_session,
        created_by=user.id,
        status=JobStatus.CLOSED,
        created_at=now - timedelta(days=60),
    )

    # Draft job is never live → excluded from the table.
    draft = await _seed_job(db_session, created_by=user.id, status=JobStatus.DRAFT)

    resp = await client.get("/dashboard/performance", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    ordered = resp.json()["rows"]
    rows = {r["job_id"]: r for r in ordered}

    assert str(draft.id) not in rows

    u = rows[str(unhealthy.id)]
    assert u["health"] == JobHealth.UNHEALTHY.value
    assert u["candidates"] == 2  # archived stint excluded
    assert u["shortlisted"] == 1

    assert rows[str(healthy.id)]["health"] == JobHealth.HEALTHY.value
    assert rows[str(closed.id)]["health"] == JobHealth.HEALTHY.value

    # Worst health floats to the top.
    assert ordered[0]["job_id"] == str(unhealthy.id)


# --- Candidate pipeline -----------------------------------------------------


async def test_pipeline_requires_auth(migrations_applied: None, client: httpx.AsyncClient) -> None:
    resp = await client.get("/dashboard/pipeline")
    assert resp.status_code == 401


async def test_pipeline_buckets_and_conversion(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    """Stages collapse to the four buckets; conversion is offers/applied."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id, status=JobStatus.OPEN)

    # Sourcing = vector_screen + hard_filter (2). Screening = whatsapp (1).
    await _seed_full_application(db_session, job_id=job.id, stage=ApplicationStage.VECTOR_SCREEN)
    await _seed_full_application(db_session, job_id=job.id, stage=ApplicationStage.HARD_FILTER)
    await _seed_full_application(db_session, job_id=job.id, stage=ApplicationStage.WHATSAPP)
    # Accepted (offer made) — counts toward conversion but not the active funnel.
    await _seed_full_application(
        db_session,
        job_id=job.id,
        stage=ApplicationStage.DONE,
        status=ApplicationStatus.ACCEPTED,
    )

    resp = await client.get(
        "/dashboard/pipeline",
        params={"job_id": str(job.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["job_id"] == str(job.id)
    assert body["by_bucket"][PipelineBucket.SOURCING.value] == 2
    assert body["by_bucket"][PipelineBucket.SCREENING.value] == 1
    assert body["by_bucket"][PipelineBucket.INTERVIEW.value] == 0
    # done@accepted is not active → not in the funnel.
    assert body["by_bucket"][PipelineBucket.FINAL_SHORTLIST.value] == 0
    assert set(body["by_bucket"]) == {b.value for b in PipelineBucket}

    # by_stage keeps the raw five-stage counts (active only, zero-filled).
    assert body["by_stage"][ApplicationStage.VECTOR_SCREEN.value] == 1
    assert body["by_stage"][ApplicationStage.HARD_FILTER.value] == 1
    assert body["by_stage"][ApplicationStage.WHATSAPP.value] == 1
    assert body["by_stage"][ApplicationStage.INTERVIEW.value] == 0
    # done@accepted is not active → not in the funnel.
    assert body["by_stage"][ApplicationStage.DONE.value] == 0
    assert set(body["by_stage"]) == {s.value for s in ApplicationStage}

    assert body["applied"] == 4
    assert body["offers"] == 1
    assert body["conversion_rate"] == 25.0


# --- Upcoming interviews ----------------------------------------------------


async def test_upcoming_interviews_requires_auth(
    migrations_applied: None, client: httpx.AsyncClient
) -> None:
    resp = await client.get("/dashboard/upcoming-interviews")
    assert resp.status_code == 401


async def test_upcoming_interviews_orders_future_only(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id, status=JobStatus.OPEN)
    now = datetime.now(UTC)

    soon = await _seed_full_application(db_session, job_id=job.id, stage=ApplicationStage.INTERVIEW)
    await _seed_booking(
        db_session, application_id=soon.id, scheduled_start_at=now + timedelta(hours=2)
    )
    later = await _seed_full_application(
        db_session, job_id=job.id, stage=ApplicationStage.INTERVIEW
    )
    await _seed_booking(
        db_session, application_id=later.id, scheduled_start_at=now + timedelta(days=3)
    )
    # A past booking must not appear.
    past = await _seed_full_application(db_session, job_id=job.id, stage=ApplicationStage.INTERVIEW)
    await _seed_booking(
        db_session, application_id=past.id, scheduled_start_at=now - timedelta(hours=2)
    )

    resp = await client.get(
        "/dashboard/upcoming-interviews",
        params={"limit": 50},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_app = {iv["application_id"]: iv for iv in body["interviews"]}

    assert str(soon.id) in by_app
    assert str(later.id) in by_app
    assert str(past.id) not in by_app

    # Soonest first.
    ordered = [iv["application_id"] for iv in body["interviews"]]
    assert ordered.index(str(soon.id)) < ordered.index(str(later.id))

    iv = by_app[str(soon.id)]
    assert iv["job_title"] == job.title
    assert iv["join_url"] == "https://zoom.invalid/j/123"
    assert body["total"] >= 2


async def test_upcoming_interviews_limit_caps_results(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id, status=JobStatus.OPEN)
    now = datetime.now(UTC)
    for i in range(3):
        app = await _seed_full_application(
            db_session, job_id=job.id, stage=ApplicationStage.INTERVIEW
        )
        await _seed_booking(
            db_session, application_id=app.id, scheduled_start_at=now + timedelta(days=i + 1)
        )

    resp = await client.get(
        "/dashboard/upcoming-interviews",
        params={"limit": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["interviews"]) == 1


# --- Pending feedback -------------------------------------------------------


async def test_pending_feedback_requires_auth(
    migrations_applied: None, client: httpx.AsyncClient
) -> None:
    resp = await client.get("/dashboard/pending-feedback")
    assert resp.status_code == 401


async def test_pending_feedback_surfaces_stalled_interviews(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id, status=JobStatus.OPEN)
    now = datetime.now(UTC)

    stalled = await _seed_full_application(
        db_session,
        job_id=job.id,
        stage=ApplicationStage.INTERVIEW,
        stage_updated_at=now - timedelta(days=INTERVIEW_PENDING_FEEDBACK_DAYS + 2),
    )
    # Fresh interview app — within SLA, should not appear.
    fresh = await _seed_full_application(
        db_session,
        job_id=job.id,
        stage=ApplicationStage.INTERVIEW,
        stage_updated_at=now,
    )
    # Stalled but not at interview stage — irrelevant.
    other_stage = await _seed_full_application(
        db_session,
        job_id=job.id,
        stage=ApplicationStage.WHATSAPP,
        stage_updated_at=now - timedelta(days=INTERVIEW_PENDING_FEEDBACK_DAYS + 2),
    )

    resp = await client.get(
        "/dashboard/pending-feedback", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    items = {it["application_id"]: it for it in resp.json()["items"]}

    assert str(stalled.id) in items
    assert str(fresh.id) not in items
    assert str(other_stage.id) not in items

    assert items[str(stalled.id)]["stage"] == ApplicationStage.INTERVIEW.value
    assert items[str(stalled.id)]["days_waiting"] >= INTERVIEW_PENDING_FEEDBACK_DAYS
    assert items[str(stalled.id)]["job_title"] == job.title
