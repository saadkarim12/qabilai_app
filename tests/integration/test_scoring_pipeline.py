"""Integration tests for the Step 4.3 hard-filter scoring Celery task.

Drives ``score_hard_filter_for_application`` directly with the per-test
transactional ``db_session`` so writes roll back at teardown. The
``score_hard_filters`` service function is monkeypatched to a
deterministic result so we don't touch Anthropic.

Doc Step 4.3 acceptance criteria covered:

* Sample CV vs JD produces 5 signal scores (delegated to unit tests).
* Weighted total saved to ``applications.hard_filter_score``.
* Breakdown JSON includes reasoning per signal in
  ``applications.hard_filter_breakdown``.
* Failed Claude output triggers retry, then surfaces error via app
  status — i.e. ``pipeline_status['hard_filter'] = 'failed'`` with an
  error key (the retry itself is unit-tested in
  ``tests/unit/test_hard_filter_score.py``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.application import Application
from src.db.models.application_score import ApplicationScore
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.job import Job
from src.db.models.user import User
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.events import AppEventState, AppEventType
from src.enums.hard_filters import HARD_FILTER_WEIGHTS, HardFilterKey
from src.enums.jobs import EmploymentType, JobStatus, NoticePeriod, WorkMode
from src.enums.roles import UserRole
from src.enums.score_types import ScoreModel, ScoreType
from src.schemas.events import AppEvent
from src.schemas.hard_filter import HardFilterScoreResult, HardFilterSignalDetail
from src.utils.password import hash_password
from src.utils.slug import generate_public_slug
from src.workers.tasks import scoring as scoring_task
from src.workers.tasks.scoring import (
    HARD_FILTER_MAX_RETRIES,
    score_hard_filter_for_application,
    score_hard_filters_task,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_admin(db_session: AsyncSession) -> User:
    user = User(
        email=f"hr-{uuid.uuid4().hex[:8]}@kabil.dev",
        password_hash=hash_password("hunter2-correct"),
        full_name="HR Person",
        role=UserRole.ADMIN,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _seed_job(db_session: AsyncSession, *, created_by: uuid.UUID) -> Job:
    job = Job(
        created_by=created_by,
        title="Senior Backend Engineer",
        hiring_company="Kabil Test Co",
        country="AE",
        city="Dubai",
        employment_type=EmploymentType.PERMANENT,
        work_mode=WorkMode.HYBRID,
        currency="AED",
        min_salary=20_000,
        max_salary=30_000,
        notice_period=NoticePeriod.DAYS_30,
        min_experience_years=5,
        required_skills=["Python", "PostgreSQL"],
        preferred_skills=["AWS"],
        nationality_preference=[],
        languages_required=["english"],
        job_description="Build the Kabil hiring backend.",
        status=JobStatus.OPEN,
        public_slug=generate_public_slug(),
    )
    db_session.add(job)
    await db_session.flush()
    return job


async def _seed_candidate(
    db_session: AsyncSession,
    *,
    parsed_profile: dict | None = None,
) -> Candidate:
    profile = (
        parsed_profile
        if parsed_profile is not None
        else {
            "full_name": "Layla Hassan",
            "email": "layla.hassan@example.com",
            "phone": "+971501234567",
            "skills": ["Python", "PostgreSQL", "FastAPI"],
            "work_history": [],
            "total_experience_years": 6.5,
            "education": [],
            "languages": ["English"],
            "parse_status": "ok",
        }
    )
    cand = Candidate(
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        phone_e164="+971501234567",
        full_name="Test Candidate",
        parsed_profile=profile,
    )
    db_session.add(cand)
    await db_session.flush()
    return cand


async def _seed_cv_doc(
    db_session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    extracted_text: str | None = "Cut p95 latency 800ms -> 200ms across 4 services.",
) -> CvDocument:
    cv = CvDocument(
        candidate_id=candidate_id,
        blob_url="https://blob.invalid/test.pdf",
        blob_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        is_current=True,
        extracted_text=extracted_text,
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
    stage: ApplicationStage = ApplicationStage.HARD_FILTER,
    hard_filter_score: float | None = None,
) -> Application:
    app = Application(
        job_id=job_id,
        candidate_id=candidate_id,
        cv_document_id=cv_document_id,
        stage=stage,
        status=ApplicationStatus.ACTIVE,
        consent_context={"ip": "127.0.0.1", "user_agent": "pytest"},
        consented_at=datetime.now(UTC),
        hard_filter_score=hard_filter_score,
    )
    db_session.add(app)
    await db_session.flush()
    return app


def _fake_result(total: float = 76.0) -> HardFilterScoreResult:
    """Deterministic result used by the monkeypatched scoring service."""
    signals: dict[HardFilterKey, HardFilterSignalDetail] = {}
    # Reasoning text varies per signal so we can assert breakdown round-trip.
    reasonings = {
        HardFilterKey.SKILLS: "Strong match on Python and Postgres.",
        HardFilterKey.EXPERIENCE: "6 yrs vs 5 required.",
        HardFilterKey.INDUSTRY: "Adjacent SaaS background.",
        HardFilterKey.REGIONAL: "Same time zone.",
        HardFilterKey.ROLE_SENIORITY: "Senior IC track aligned.",
    }
    for key in HardFilterKey:
        signals[key] = HardFilterSignalDetail(
            score=total,  # arbitrary uniform per-signal value
            reasoning=reasonings[key],
            evidence=f"CV quote grounding {key.value}.",
            gap=None,
            weight=HARD_FILTER_WEIGHTS[key],
        )
    return HardFilterScoreResult(
        total=total,
        signals=signals,
        prompt_version="hard_filter_score:base:v1",
        model_used=ScoreModel.CLAUDE_SONNET.value,
    )


async def _seed_full(db_session: AsyncSession) -> Application:
    user = await _seed_admin(db_session)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    return await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )


# ---------------------------------------------------------------------------
# Realtime-event spy
# ---------------------------------------------------------------------------


def _spy_published(monkeypatch: pytest.MonkeyPatch) -> list[AppEvent]:
    """Capture the envelopes the task would publish, bypassing Redis.

    Patches the publisher *as referenced inside the scoring module* so the
    real pub/sub never runs; the test asserts on the captured envelopes.
    """
    captured: list[AppEvent] = []

    async def _capture(event: AppEvent) -> None:
        captured.append(event)

    monkeypatch.setattr(scoring_task, "publish_app_event", _capture)
    return captured


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


async def test_success_persists_score_breakdown_and_audit_row(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc acceptance: weighted total saved + breakdown JSON includes reasoning."""
    app = await _seed_full(db_session)
    expected = _fake_result(total=76.5)

    async def _fake_score(**kwargs: object) -> HardFilterScoreResult:
        return expected

    monkeypatch.setattr(scoring_task, "score_hard_filters", _fake_score)
    published = _spy_published(monkeypatch)

    await score_hard_filter_for_application(app.id, session=db_session)

    # A single OK nudge fires after the commit, pointing at this application.
    assert len(published) == 1
    assert published[0].event is AppEventType.HARD_FILTER
    assert published[0].state is AppEventState.OK
    assert published[0].application_id == app.id
    assert published[0].job_id == app.job_id

    # The helper persists via raw UPDATE which doesn't propagate JSONB
    # column values back to in-memory ORM objects. Issue a fresh SELECT
    # that bypasses the identity map so the assertions see the DB state.
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app.id))
    ).scalar_one_or_none()
    assert refreshed is not None
    assert refreshed.hard_filter_score == pytest.approx(76.5)
    assert refreshed.hard_filter_breakdown is not None
    # Every signal appears with score + reasoning + evidence + weight.
    for key in HardFilterKey:
        entry = refreshed.hard_filter_breakdown[key.value]
        assert entry["score"] == pytest.approx(76.5)
        assert entry["reasoning"]
        assert entry["evidence"]
        assert "gap" in entry
        assert entry["weight"] == HARD_FILTER_WEIGHTS[key]

    # pipeline_status['hard_filter'] = 'ok'; no error key.
    assert refreshed.pipeline_status["hard_filter"] == "ok"
    assert "hard_filter_error" not in refreshed.pipeline_status

    # Stage and status are NOT modified.
    assert refreshed.stage is ApplicationStage.HARD_FILTER
    assert refreshed.status is ApplicationStatus.ACTIVE

    # ApplicationScore audit row appended.
    audit_rows = (
        (
            await db_session.execute(
                sa.select(ApplicationScore).where(ApplicationScore.application_id == app.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row.score_type is ScoreType.HARD_FILTER
    assert row.value == pytest.approx(76.5)
    assert row.model_used == ScoreModel.CLAUDE_SONNET.value
    # The audit breakdown is the full per-signal model_dump (score + reasoning
    # + evidence + gap + weight), nested under a ``signals`` key.
    assert row.breakdown == {
        "signals": {
            key.value: entry.model_dump(mode="json") for key, entry in expected.signals.items()
        }
    }


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_idempotent_skip_when_score_already_present(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-delivery on an already-scored row reconciles status, no Claude call."""
    user = await _seed_admin(db_session)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
        hard_filter_score=80.0,
    )

    calls: list[object] = []

    async def _fake_score(**kwargs: object) -> HardFilterScoreResult:
        calls.append(kwargs)
        return _fake_result()

    monkeypatch.setattr(scoring_task, "score_hard_filters", _fake_score)

    await score_hard_filter_for_application(app.id, session=db_session)

    assert calls == []  # service never called
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app.id))
    ).scalar_one_or_none()
    assert refreshed is not None
    assert refreshed.hard_filter_score == pytest.approx(80.0)  # unchanged
    assert refreshed.pipeline_status["hard_filter"] == "ok"


# ---------------------------------------------------------------------------
# Permanent content failure
# ---------------------------------------------------------------------------


async def test_permanent_claude_failure_marks_pipeline_status_failed(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc acceptance: 'Failed Claude output ... surfaces error via app status'."""
    from src.services.scoring.hard_filter_service import HardFilterScoringError

    app = await _seed_full(db_session)

    async def _fake_score(**kwargs: object) -> HardFilterScoreResult:
        raise HardFilterScoringError("claude_failed_twice: schema_validation")

    monkeypatch.setattr(scoring_task, "score_hard_filters", _fake_score)
    published = _spy_published(monkeypatch)

    await score_hard_filter_for_application(app.id, session=db_session)

    # A single FAILED nudge fires so a waiting FE stops spinning.
    assert len(published) == 1
    assert published[0].event is AppEventType.HARD_FILTER
    assert published[0].state is AppEventState.FAILED
    assert published[0].application_id == app.id

    # The helper persists via raw UPDATE which doesn't propagate JSONB
    # column values back to in-memory ORM objects. Issue a fresh SELECT
    # that bypasses the identity map so the assertions see the DB state.
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app.id))
    ).scalar_one_or_none()
    assert refreshed is not None
    assert refreshed.hard_filter_score is None  # nothing persisted
    assert refreshed.hard_filter_breakdown is None
    assert refreshed.pipeline_status["hard_filter"] == "failed"
    assert "claude_failed_twice" in refreshed.pipeline_status["hard_filter_error"]
    # Stage/status NOT changed despite the failure.
    assert refreshed.stage is ApplicationStage.HARD_FILTER
    assert refreshed.status is ApplicationStatus.ACTIVE


# ---------------------------------------------------------------------------
# Missing inputs
# ---------------------------------------------------------------------------


async def test_missing_parsed_profile_marks_failed(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _seed_admin(db_session)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session, parsed_profile={})  # empty profile
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    async def _fake_score(**kwargs: object) -> HardFilterScoreResult:
        raise AssertionError("score_hard_filters should not be called")

    monkeypatch.setattr(scoring_task, "score_hard_filters", _fake_score)

    await score_hard_filter_for_application(app.id, session=db_session)

    # The helper persists via raw UPDATE which doesn't propagate JSONB
    # column values back to in-memory ORM objects. Issue a fresh SELECT
    # that bypasses the identity map so the assertions see the DB state.
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app.id))
    ).scalar_one_or_none()
    assert refreshed is not None
    assert refreshed.pipeline_status["hard_filter"] == "failed"
    assert refreshed.pipeline_status["hard_filter_error"] == "parsed_profile_missing"


async def test_missing_extracted_text_marks_failed(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _seed_admin(db_session)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id, extracted_text=None)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    async def _fake_score(**kwargs: object) -> HardFilterScoreResult:
        raise AssertionError("score_hard_filters should not be called")

    monkeypatch.setattr(scoring_task, "score_hard_filters", _fake_score)

    await score_hard_filter_for_application(app.id, session=db_session)

    # The helper persists via raw UPDATE which doesn't propagate JSONB
    # column values back to in-memory ORM objects. Issue a fresh SELECT
    # that bypasses the identity map so the assertions see the DB state.
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app.id))
    ).scalar_one_or_none()
    assert refreshed is not None
    assert refreshed.pipeline_status["hard_filter"] == "failed"
    assert refreshed.pipeline_status["hard_filter_error"] == "extracted_text_missing"


async def test_unknown_application_is_a_noop(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vanished application id logs and returns; no exception."""

    async def _fake_score(**kwargs: object) -> HardFilterScoreResult:
        raise AssertionError("score_hard_filters should not be called")

    monkeypatch.setattr(scoring_task, "score_hard_filters", _fake_score)

    # Should not raise.
    await score_hard_filter_for_application(uuid.uuid4(), session=db_session)


# ---------------------------------------------------------------------------
# Celery wrapper retry config
# ---------------------------------------------------------------------------


def test_retry_kwargs_match_doc() -> None:
    """Doc Step 4.3: scoring respects the standard 3-retry policy."""
    task = score_hard_filters_task
    assert task.max_retries == HARD_FILTER_MAX_RETRIES
    # retry_backoff is set so transient errors don't hammer Anthropic.
    assert task.retry_backoff is True


# ---------------------------------------------------------------------------
# Step 4.4: force=True (rescore) path
# ---------------------------------------------------------------------------


async def test_force_true_bypasses_idempotent_skip_and_nulls_old_score(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rescore on an already-scored row re-runs and overwrites the value."""
    user = await _seed_admin(db_session)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
        hard_filter_score=50.0,
    )

    calls: list[dict[str, object]] = []

    async def _fake_score(**kwargs: object) -> HardFilterScoreResult:
        calls.append(kwargs)
        return _fake_result(total=92.0)

    monkeypatch.setattr(scoring_task, "score_hard_filters", _fake_score)

    await score_hard_filter_for_application(app.id, session=db_session, force=True)

    # Service was called (skip bypassed), with use_cache=False so Claude
    # doesn't return a stale cached response.
    assert len(calls) == 1
    assert calls[0].get("use_cache") is False

    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app.id))
    ).scalar_one_or_none()
    assert refreshed is not None
    assert refreshed.hard_filter_score == pytest.approx(92.0)


async def test_force_false_default_still_skips_when_score_present(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity guard: the existing idempotent-skip behaviour is unchanged."""
    user = await _seed_admin(db_session)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
        hard_filter_score=50.0,
    )

    calls: list[dict[str, object]] = []

    async def _fake_score(**kwargs: object) -> HardFilterScoreResult:
        calls.append(kwargs)
        return _fake_result(total=92.0)

    monkeypatch.setattr(scoring_task, "score_hard_filters", _fake_score)

    await score_hard_filter_for_application(app.id, session=db_session)  # force defaults False

    assert calls == []  # idempotent skip
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app.id))
    ).scalar_one_or_none()
    assert refreshed is not None
    assert refreshed.hard_filter_score == pytest.approx(50.0)  # unchanged
