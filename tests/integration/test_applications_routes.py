"""Integration tests for the Step 4.1 ``/applications`` routes.

Covers the architecture-doc acceptance criteria:

* Lists filter by stage and status.
* Detail returns full nested object (candidate + cv + scores).
* Audit log returns chronological actions (empty here; populated in 4.2).

Plus the negative paths the route layer protects against: missing auth,
unknown job, unknown application, page-size cap.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.application import Application
from src.db.models.application_score import ApplicationScore
from src.db.models.audit_log import AuditLog
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.job import Job
from src.db.models.user import User
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.audit import AuditAction, AuditEntityType
from src.enums.authenticity import AuthenticityBand
from src.enums.jobs import EmploymentType, JobStatus, WorkMode
from src.enums.roles import UserRole
from src.enums.score_types import ScoreModel, ScoreType
from src.services import application_service
from src.utils.password import hash_password

pytestmark = pytest.mark.integration


# --- Fake Redis (mirrors test_jobs.py) ---------------------------------------


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.deleted_keys: list[str] = []

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: str | bytes, ex: int | None = None) -> None:
        self.store[key] = value.encode() if isinstance(value, str) else value

    async def scan_iter(self, match: str, count: int = 100) -> Any:
        # Convert glob ``*`` to a simple prefix match — only ``prefix:*``
        # shapes are used by the service, so we don't need a real glob.
        if match.endswith("*"):
            prefix = match[:-1]
            for key in list(self.store.keys()):
                if key.startswith(prefix):
                    yield key
        else:
            if match in self.store:
                yield match

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                removed += 1
                self.deleted_keys.append(key)
        return removed


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(application_service, "_redis_client", lambda: fake)
    return fake


# --- Seed helpers ------------------------------------------------------------


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
        min_experience_years=3,
        job_description="Build stuff with Python.",
        status=JobStatus.OPEN,
        public_slug=uuid.uuid4().hex[:16],
    )
    db_session.add(job)
    await db_session.flush()
    return job


async def _seed_candidate(db_session: AsyncSession, *, name: str = "Test Candidate") -> Candidate:
    cand = Candidate(
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        phone_e164=f"+9715{uuid.uuid4().hex[:7]}",
        full_name=name,
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
    similarity_score: float | None = None,
) -> Application:
    app = Application(
        job_id=job_id,
        candidate_id=candidate_id,
        cv_document_id=cv_document_id,
        stage=stage,
        status=status,
        consent_context={"ip": "127.0.0.1", "user_agent": "pytest"},
        consented_at=datetime.now(UTC),
        similarity_score=similarity_score,
    )
    db_session.add(app)
    await db_session.flush()
    return app


# --- Auth gates --------------------------------------------------------------


async def test_list_requires_auth(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get(f"/jobs/{uuid.uuid4()}/applications")
    assert resp.status_code == 401


async def test_detail_requires_auth(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get(f"/applications/{uuid.uuid4()}")
    assert resp.status_code == 401


async def test_audit_log_requires_auth(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get(f"/applications/{uuid.uuid4()}/audit-log")
    assert resp.status_code == 401


# --- Listing -----------------------------------------------------------------


async def test_list_unknown_job_returns_404(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    resp = await client.get(
        f"/jobs/{uuid.uuid4()}/applications",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_list_empty_for_job_with_no_applications(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)

    resp = await client.get(
        f"/jobs/{job.id}/applications",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["page"] == 1


async def test_list_returns_applications_with_candidate_name(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session, name="Alice Example")
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
        similarity_score=82.0,
    )

    resp = await client.get(
        f"/jobs/{job.id}/applications",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["id"] == str(app.id)
    assert item["candidate_full_name"] == "Alice Example"
    assert item["candidate_email"] == cand.email
    assert item["similarity_score"] == "82%"
    # No interview mark for an L1 applicant.
    assert item["interview_score"] is None
    assert item["stage"] == ApplicationStage.VECTOR_SCREEN.value
    assert item["status"] == ApplicationStatus.ACTIVE.value


async def test_list_exposes_interview_score(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    """The list row surfaces the interview mark so the board can sort by it."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session, name="Bob Interview")
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
        stage=ApplicationStage.INTERVIEW,
    )
    app.interview_score = 76.0
    await db_session.flush()

    resp = await client.get(
        f"/jobs/{job.id}/applications",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["interview_score"] == "76%"


async def test_list_filters_by_status(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)

    # Two candidates, two CVs, two applications — one ACTIVE one REJECTED.
    for status in (ApplicationStatus.ACTIVE, ApplicationStatus.REJECTED):
        cand = await _seed_candidate(db_session)
        cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
        await _seed_application(
            db_session,
            job_id=job.id,
            candidate_id=cand.id,
            cv_document_id=cv.id,
            status=status,
        )

    resp = await client.get(
        f"/jobs/{job.id}/applications?status=rejected",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "rejected"


async def test_list_filters_by_stage(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    resp = await client.get(
        f"/jobs/{job.id}/applications?stage=vector_screen",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    # An unrelated stage filter would only work once 4.2 extends the
    # ``ApplicationStage`` enum; today vector_screen is the only allowed
    # value, so we just confirm the round-trip on the supported one.


async def test_list_orders_by_similarity_desc(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)

    # Two applications with different similarities.
    high = await _seed_candidate(db_session, name="HIGH score")
    low = await _seed_candidate(db_session, name="LOW score")
    high_cv = await _seed_cv_doc(db_session, candidate_id=high.id)
    low_cv = await _seed_cv_doc(db_session, candidate_id=low.id)
    await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=high.id,
        cv_document_id=high_cv.id,
        similarity_score=95.0,
    )
    await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=low.id,
        cv_document_id=low_cv.id,
        similarity_score=21.0,
    )

    resp = await client.get(
        f"/jobs/{job.id}/applications?order=-similarity_score",
        headers={"Authorization": f"Bearer {token}"},
    )
    body = resp.json()
    assert [it["similarity_score"] for it in body["items"]] == ["95%", "21%"]


async def test_list_page_size_cap(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)

    resp = await client.get(
        f"/jobs/{job.id}/applications?page_size=101",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


# --- Detail ------------------------------------------------------------------


async def test_detail_returns_full_nested_object(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session, name="Detail Test")
    # Populate the explainability fields so we can assert the detail
    # endpoint surfaces authenticity reasoning + the parsed CV profile.
    cand.authenticity_score = 69.33
    cand.authenticity_band = AuthenticityBand.REVIEW
    cand.authenticity_computed_at = datetime(2026, 5, 26, tzinfo=UTC)
    cand.authenticity_breakdown = {
        "score": 69.33,
        "band": AuthenticityBand.REVIEW.value,
        "breakdown": {
            "specificity": {
                "score": 72.0,
                "computed_by": "claude",
                "finding": "Most work bullets include concrete metrics.",
                "concern": False,
                "reasons": ["Most work bullets include concrete metrics."],
                "details": {},
            }
        },
        "top_concerns": [],
        "rationale": "Reads authentic overall.",
    }
    cand.parsed_profile = {
        "skills": ["Python", "FastAPI"],
        "total_experience_years": 1.5,
    }
    await db_session.flush()
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
        similarity_score=71.0,
    )

    # Add a score history row to assert the nested ``scores`` array works.
    db_session.add(
        ApplicationScore(
            application_id=app.id,
            score_type=ScoreType.SIMILARITY,
            value=71.0,
            breakdown={},
            prompt_version="-",
            model_used=ScoreModel.OPENAI_EMBED_3_SMALL.value,
        )
    )
    await db_session.flush()

    resp = await client.get(
        f"/applications/{app.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()

    assert body["id"] == str(app.id)
    assert body["job_id"] == str(job.id)
    assert body["candidate"]["full_name"] == "Detail Test"
    assert body["candidate"]["email"] == cand.email
    assert body["cv_document"]["id"] == str(cv.id)
    assert body["similarity_score"] == "71%"

    # Authenticity headline + parsed CV profile are surfaced on the candidate;
    # the per-signal breakdown now lives in the ``scores`` list instead.
    assert body["candidate"]["authenticity_score"] == "69.33%"
    assert body["candidate"]["authenticity_band"] == AuthenticityBand.REVIEW.value
    assert body["candidate"]["authenticity_computed_at"] is not None
    assert "authenticity_breakdown" not in body["candidate"]
    assert body["candidate"]["parsed_profile"]["total_experience_years"] == 1.5

    # The hard-filter breakdown is no longer duplicated at the top level — it
    # lives only in the ``scores`` list.
    assert "hard_filter_breakdown" not in body

    # Score history nested: the persisted similarity row plus the synthesized
    # authenticity entry, both newest-first.
    assert len(body["scores"]) == 2
    by_type = {s["score_type"]: s for s in body["scores"]}

    similarity_entry = by_type[ScoreType.SIMILARITY.value]
    assert similarity_entry["model_used"] == ScoreModel.OPENAI_EMBED_3_SMALL.value

    # Authenticity is synthesized (no backing application_scores id). With no
    # application-level score yet, it falls back to the candidate baseline;
    # the Sonnet judge produces it, and its per-signal breakdown is nested.
    auth_entry = by_type[ScoreType.AUTHENTICITY.value]
    assert auth_entry["id"] is None
    assert auth_entry["value"] == "69.33%"
    assert auth_entry["model_used"] == ScoreModel.CLAUDE_SONNET.value
    signal = auth_entry["breakdown"]["breakdown"]["specificity"]
    assert signal["score"] == "72%"
    assert signal["finding"] == "Most work bullets include concrete metrics."


async def test_detail_unknown_application_returns_404(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    resp = await client.get(
        f"/applications/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# --- Audit log ---------------------------------------------------------------


async def test_audit_log_empty_for_new_application(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    resp = await client.get(
        f"/applications/{app.id}/audit-log",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_audit_log_returns_chronological_entries(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    # Two audit log entries with explicit timestamps so the ORDER BY
    # ``created_at DESC`` tiebreak is deterministic — Postgres' ``now()``
    # is statement-stable within a transaction so without explicit
    # timestamps both rows would share a ``created_at`` and the tiebreak
    # on id (random UUID) would flip the assertion order randomly.
    t1 = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 5, 1, 11, 0, 0, tzinfo=UTC)
    first = AuditLog(
        user_id=user.id,
        entity_type=AuditEntityType.APPLICATION.value,
        entity_id=app.id,
        action=AuditAction.STAGE_CHANGED.value,
        after_state={"stage": "hard_filter"},
        created_at=t1,
    )
    db_session.add(first)
    await db_session.flush()

    second = AuditLog(
        user_id=user.id,
        entity_type=AuditEntityType.APPLICATION.value,
        entity_id=app.id,
        action=AuditAction.STATUS_CHANGED.value,
        before_state={"status": "active"},
        after_state={"status": "rejected"},
        created_at=t2,
    )
    db_session.add(second)
    await db_session.flush()

    resp = await client.get(
        f"/applications/{app.id}/audit-log",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    # Newest first.
    assert body["items"][0]["id"] == str(second.id)
    assert body["items"][0]["action"] == AuditAction.STATUS_CHANGED.value
    assert body["items"][1]["id"] == str(first.id)


async def test_audit_log_unknown_application_returns_404(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    resp = await client.get(
        f"/applications/{uuid.uuid4()}/audit-log",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# --- Step 4.2: PATCH /stage --------------------------------------------------


async def _patch_stage(
    client: httpx.AsyncClient,
    token: str,
    application_id: uuid.UUID | str,
    *,
    stage: str,
    reason: str | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {"stage": stage}
    if reason is not None:
        body["reason"] = reason
    return await client.patch(
        f"/applications/{application_id}/stage",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )


async def _patch_status(
    client: httpx.AsyncClient,
    token: str,
    application_id: uuid.UUID | str,
    *,
    status: str,
    reason: str | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {"status": status}
    if reason is not None:
        body["reason"] = reason
    return await client.patch(
        f"/applications/{application_id}/status",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )


async def test_patch_stage_requires_auth(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    resp = await client.patch(f"/applications/{uuid.uuid4()}/stage", json={"stage": "hard_filter"})
    assert resp.status_code == 401


async def test_patch_status_requires_auth(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    resp = await client.patch(f"/applications/{uuid.uuid4()}/status", json={"status": "rejected"})
    assert resp.status_code == 401


async def test_patch_stage_vector_screen_to_hard_filter_succeeds(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The doc's primary acceptance: moving to hard_filter must dispatch the task."""
    dispatched: list[str] = []
    from src.api.routes import applications as application_routes

    monkeypatch.setattr(
        application_routes.score_hard_filters_task,
        "apply_async",
        lambda args=None, kwargs=None: dispatched.append(args[0] if args else ""),
    )

    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    resp = await _patch_stage(client, token, app.id, stage="hard_filter", reason="Looks promising")
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["stage"] == ApplicationStage.HARD_FILTER.value
    assert detail["status"] == ApplicationStatus.ACTIVE.value
    assert dispatched == [str(app.id)]


async def test_patch_stage_writes_audit_log(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc acceptance: 'Audit log shows transition'."""
    from src.api.routes import applications as application_routes

    monkeypatch.setattr(
        application_routes.score_hard_filters_task,
        "apply_async",
        lambda args=None, kwargs=None: None,
    )

    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    resp = await _patch_stage(client, token, app.id, stage="hard_filter", reason="Top similarity")
    assert resp.status_code == 200, resp.text

    log_resp = await client.get(
        f"/applications/{app.id}/audit-log",
        headers={"Authorization": f"Bearer {token}"},
    )
    log_body = log_resp.json()
    assert log_body["total"] == 1
    entry = log_body["items"][0]
    assert entry["action"] == AuditAction.STAGE_CHANGED.value
    assert entry["before_state"] == {"stage": "vector_screen"}
    assert entry["after_state"]["stage"] == "hard_filter"
    assert entry["after_state"]["reason"] == "Top similarity"
    assert entry["user_id"] == str(user.id)


async def test_patch_stage_invalid_edge_returns_422_with_reason(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    """Doc acceptance: 'Invalid transition returns 422 with reason'."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
        stage=ApplicationStage.VECTOR_SCREEN,
    )

    # vector_screen → whatsapp is not in the allow-list (must go through
    # hard_filter first).
    resp = await _patch_stage(client, token, app.id, stage="whatsapp")
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"] == "illegal_stage_transition"
    assert "vector_screen" in body["message"]
    assert "whatsapp" in body["message"]


async def test_patch_stage_to_same_stage_returns_422(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    resp = await _patch_stage(client, token, app.id, stage="vector_screen")
    assert resp.status_code == 422


async def test_patch_stage_blocked_when_status_is_rejected(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    """Stage moves require status=ACTIVE — rejected applications are frozen."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
        status=ApplicationStatus.REJECTED,
    )

    resp = await _patch_stage(client, token, app.id, stage="hard_filter")
    assert resp.status_code == 422
    assert resp.json()["error"] == "illegal_stage_transition"


async def test_patch_stage_unknown_application_returns_404(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    resp = await _patch_stage(client, token, uuid.uuid4(), stage="hard_filter")
    assert resp.status_code == 404


async def test_patch_stage_invalidates_list_cache(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc requirement: 'Invalidate application detail + list cache on transition'."""
    from src.api.routes import applications as application_routes

    monkeypatch.setattr(
        application_routes.score_hard_filters_task,
        "apply_async",
        lambda args=None, kwargs=None: None,
    )

    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    # Prime the cache via a list call.
    list_resp = await client.get(
        f"/jobs/{job.id}/applications",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_resp.status_code == 200
    assert len(fake_redis.store) >= 1

    # Transition should clear the cached key for this job.
    resp = await _patch_stage(client, token, app.id, stage="hard_filter")
    assert resp.status_code == 200
    assert len(fake_redis.deleted_keys) >= 1
    # And no leftover keys remain for that job_id prefix.
    leftover = [k for k in fake_redis.store if str(job.id) in k]
    assert leftover == []


# --- Step 4.2: PATCH /status -------------------------------------------------


async def test_patch_status_active_to_rejected_succeeds(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    resp = await _patch_status(client, token, app.id, status="rejected", reason="Bad fit")
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["status"] == ApplicationStatus.REJECTED.value
    # Stage stays the same — the doc requires the rejection point to be visible.
    assert detail["stage"] == ApplicationStage.VECTOR_SCREEN.value

    # Audit row written with MANUALLY_REJECTED action.
    log_resp = await client.get(
        f"/applications/{app.id}/audit-log",
        headers={"Authorization": f"Bearer {token}"},
    )
    items = log_resp.json()["items"]
    assert items[0]["action"] == AuditAction.MANUALLY_REJECTED.value
    assert items[0]["before_state"]["status"] == "active"
    assert items[0]["after_state"]["status"] == "rejected"


async def test_patch_status_active_to_accepted_succeeds(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    resp = await _patch_status(client, token, app.id, status="accepted")
    assert resp.status_code == 200
    assert resp.json()["status"] == ApplicationStatus.ACCEPTED.value


async def test_patch_status_rejected_to_active_override_path(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    """Doc 'HR override' path: rejected back to active for the same stage."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
        status=ApplicationStatus.REJECTED,
    )

    resp = await _patch_status(client, token, app.id, status="active", reason="False positive")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == ApplicationStatus.ACTIVE.value
    # Stage unchanged — HR resumes from where the candidate was rejected.
    assert resp.json()["stage"] == ApplicationStage.VECTOR_SCREEN.value


async def test_patch_status_accepted_is_terminal(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    """Once accepted, no further status changes."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
        status=ApplicationStatus.ACCEPTED,
    )

    for target in ("active", "rejected"):
        resp = await _patch_status(client, token, app.id, status=target)
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"] == "illegal_application_status_transition"


async def test_patch_status_invalid_edge_returns_422(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    """rejected → accepted is not in the matrix."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
        status=ApplicationStatus.REJECTED,
    )

    resp = await _patch_status(client, token, app.id, status="accepted")
    assert resp.status_code == 422
    assert resp.json()["error"] == "illegal_application_status_transition"


async def test_patch_status_unknown_application_returns_404(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    resp = await _patch_status(client, token, uuid.uuid4(), status="rejected")
    assert resp.status_code == 404


async def test_full_stage_chain_walks_to_done(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: vector_screen → hard_filter → whatsapp → interview → done."""
    from src.api.routes import applications as application_routes

    monkeypatch.setattr(
        application_routes.score_hard_filters_task,
        "apply_async",
        lambda args=None, kwargs=None: None,
    )

    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    for target in ("hard_filter", "whatsapp", "interview", "done"):
        resp = await _patch_stage(client, token, app.id, stage=target)
        assert resp.status_code == 200, f"transition to {target}: {resp.text}"
        assert resp.json()["stage"] == target

    # Done is terminal — no further forward moves.
    resp = await _patch_stage(client, token, app.id, stage="hard_filter")
    assert resp.status_code == 422


# --- Step 4.4: POST /applications/{id}/rescore ------------------------------


async def _post_rescore(
    client: httpx.AsyncClient,
    token: str,
    application_id: uuid.UUID | str,
    *,
    rescore_type: str,
) -> httpx.Response:
    return await client.post(
        f"/applications/{application_id}/rescore",
        params={"type": rescore_type},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_rescore_requires_auth(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        f"/applications/{uuid.uuid4()}/rescore",
        params={"type": "hard_filter"},
    )
    assert resp.status_code == 401


async def test_rescore_unknown_application_returns_404(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    resp = await _post_rescore(client, token, uuid.uuid4(), rescore_type="hard_filter")
    assert resp.status_code == 404
    assert resp.json()["error"] == "application_not_found"


async def test_rescore_invalid_type_returns_422(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    resp = await _post_rescore(client, token, app.id, rescore_type="not_a_real_type")
    assert resp.status_code == 422  # Pydantic enum validation


async def test_rescore_hard_filter_dispatches_with_force_true(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """202 + Celery task enqueued with (application_id, True)."""
    dispatched: list[list[Any]] = []
    from src.api.routes import applications as application_routes

    monkeypatch.setattr(
        application_routes.score_hard_filters_task,
        "apply_async",
        lambda args=None, kwargs=None: dispatched.append(list(args or [])),
    )
    # The similarity dispatcher should NOT be called.
    similarity_calls: list[Any] = []
    monkeypatch.setattr(
        application_routes.score_similarity_task,
        "apply_async",
        lambda args=None, kwargs=None: similarity_calls.append(args),
    )

    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    resp = await _post_rescore(client, token, app.id, rescore_type="hard_filter")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["application_id"] == str(app.id)
    assert body["type"] == "hard_filter"
    assert body["enqueued"] is True

    assert dispatched == [[str(app.id), True]]
    assert similarity_calls == []


async def test_rescore_similarity_dispatches_with_force_true(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symmetric to hard_filter dispatch but via score_similarity_task."""
    from src.api.routes import applications as application_routes

    dispatched: list[list[Any]] = []
    monkeypatch.setattr(
        application_routes.score_similarity_task,
        "apply_async",
        lambda args=None, kwargs=None: dispatched.append(list(args or [])),
    )
    hard_filter_calls: list[Any] = []
    monkeypatch.setattr(
        application_routes.score_hard_filters_task,
        "apply_async",
        lambda args=None, kwargs=None: hard_filter_calls.append(args),
    )

    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    resp = await _post_rescore(client, token, app.id, rescore_type="similarity")
    assert resp.status_code == 202, resp.text
    assert resp.json()["type"] == "similarity"
    assert dispatched == [[str(app.id), True]]
    assert hard_filter_calls == []


async def test_rescore_writes_audit_log_entry(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc Step 4.4 acceptance: 'Audit logged'."""
    from src.api.routes import applications as application_routes

    monkeypatch.setattr(
        application_routes.score_hard_filters_task,
        "apply_async",
        lambda args=None, kwargs=None: None,
    )

    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    resp = await _post_rescore(client, token, app.id, rescore_type="hard_filter")
    assert resp.status_code == 202

    log_resp = await client.get(
        f"/applications/{app.id}/audit-log",
        headers={"Authorization": f"Bearer {token}"},
    )
    body = log_resp.json()
    assert body["total"] == 1
    entry = body["items"][0]
    assert entry["action"] == AuditAction.RESCORE_REQUESTED.value
    assert entry["before_state"] is None
    assert entry["after_state"] == {"type": "hard_filter"}
    assert entry["user_id"] == str(user.id)


# --- Candidate contact edit (PATCH /applications/{id}/candidate) -------------


async def _patch_candidate(
    client: httpx.AsyncClient,
    token: str,
    application_id: uuid.UUID | str,
    *,
    body: dict[str, Any],
) -> httpx.Response:
    return await client.patch(
        f"/applications/{application_id}/candidate",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )


async def test_patch_candidate_requires_auth(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    resp = await client.patch(
        f"/applications/{uuid.uuid4()}/candidate",
        json={"full_name": "A", "email": "a@b.com", "phone": None},
    )
    assert resp.status_code == 401


async def test_patch_candidate_updates_name_email_phone(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
        stage=ApplicationStage.HARD_FILTER,
    )

    resp = await _patch_candidate(
        client,
        token,
        app.id,
        body={
            "full_name": "  Jane Doe  ",
            "email": "Jane.Doe@Example.COM",
            "phone": "+971 50 123 4567",
        },
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["candidate"]["full_name"] == "Jane Doe"
    # Email lower-cased, phone normalised to E.164.
    assert detail["candidate"]["email"] == "jane.doe@example.com"
    assert detail["candidate"]["phone_e164"] == "+971501234567"

    # Audit row recorded against the application.
    log_resp = await client.get(
        f"/applications/{app.id}/audit-log",
        headers={"Authorization": f"Bearer {token}"},
    )
    body = log_resp.json()
    assert body["total"] == 1
    entry = body["items"][0]
    assert entry["action"] == AuditAction.CANDIDATE_DETAILS_UPDATED.value
    assert entry["after_state"]["full_name"] == "Jane Doe"
    assert entry["after_state"]["email"] == "jane.doe@example.com"


async def test_patch_candidate_locked_from_whatsapp_stage(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
        stage=ApplicationStage.WHATSAPP,
    )

    resp = await _patch_candidate(
        client,
        token,
        app.id,
        body={"full_name": "New Name", "email": "new@example.com", "phone": None},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"] == "candidate_contact_locked"
    assert body["details"]["stage"] == ApplicationStage.WHATSAPP.value


async def test_patch_candidate_requires_at_least_one_contact(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    resp = await _patch_candidate(
        client,
        token,
        app.id,
        body={"full_name": "No Contact", "email": None, "phone": None},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "contact_required"


async def test_patch_candidate_rejects_invalid_email(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    resp = await _patch_candidate(
        client,
        token,
        app.id,
        body={"full_name": "Bad Email", "email": "not-an-email", "phone": None},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "invalid_email"


async def test_patch_candidate_conflict_with_other_candidate(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)

    # The identity we'll try to collide with.
    other = Candidate(
        email="taken@example.com",
        phone_e164="+971509999999",
        full_name="Already Here",
    )
    db_session.add(other)
    await db_session.flush()

    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session,
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
    )

    resp = await _patch_candidate(
        client,
        token,
        app.id,
        body={
            "full_name": "Collision",
            "email": "taken@example.com",
            "phone": "+971509999999",
        },
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "candidate_identity_conflict"


async def test_patch_candidate_unknown_application_returns_404(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    resp = await _patch_candidate(
        client,
        token,
        uuid.uuid4(),
        body={"full_name": "Ghost", "email": "ghost@example.com", "phone": None},
    )
    assert resp.status_code == 404
