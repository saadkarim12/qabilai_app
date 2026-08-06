"""Integration tests for talent-pool ↔ job-pipeline move semantics.

Sourcing a pooled candidate onto a job and moving a pipelined candidate back
to the pool must behave as a *move*, not a copy:

* ``POST /talent-pool/source`` deactivates the candidate's pool entry (they
  live only in the job pipeline afterwards).
* ``POST /applications/{id}/move-to-pool`` **soft-archives** the application
  (``status=archived``, scores + WhatsApp retained as history) and (re-)adds
  the candidate to the pool.
* The full round-trip never trips the live ``(candidate, job)`` uniqueness —
  re-sourcing creates a fresh application with ``already_existed=False`` while
  the archived stint survives as history.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.application import Application
from src.db.models.application_score import ApplicationScore
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.job import Job
from src.db.models.talent_pool_entry import TalentPoolEntry
from src.db.models.user import User
from src.db.models.whatsapp import WhatsAppConversation, WhatsAppMessage
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.authenticity import AuthenticityBand
from src.enums.jobs import EmploymentType, JobStatus, WorkMode
from src.enums.roles import UserRole
from src.enums.score_types import ScoreModel, ScoreType
from src.enums.whatsapp import (
    WhatsAppConversationState,
    WhatsAppDirection,
)
from src.services import bulk_upload_service
from src.utils.password import hash_password

pytestmark = pytest.mark.integration


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


async def _seed_pool_entry(
    db_session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    added_by: uuid.UUID,
) -> TalentPoolEntry:
    now = datetime.now(UTC)
    entry = TalentPoolEntry(
        candidate_id=candidate_id,
        source_job_id=None,
        added_by=added_by,
        added_at=now,
        expires_at=now + timedelta(days=30),
        is_active=True,
    )
    db_session.add(entry)
    await db_session.flush()
    return entry


async def _seed_application(
    db_session: AsyncSession,
    *,
    job_id: uuid.UUID,
    candidate_id: uuid.UUID,
    cv_document_id: uuid.UUID,
) -> Application:
    app = Application(
        job_id=job_id,
        candidate_id=candidate_id,
        cv_document_id=cv_document_id,
        stage=ApplicationStage.VECTOR_SCREEN,
        status=ApplicationStatus.ACTIVE,
        consent_context={"ip": "127.0.0.1", "user_agent": "pytest"},
        consented_at=datetime.now(UTC),
    )
    db_session.add(app)
    await db_session.flush()
    return app


@pytest.fixture
def no_enqueue(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Stub the post-commit CV-pipeline dispatch so no Celery broker is hit."""
    captured: list[uuid.UUID] = []

    def _fake_enqueue(application_id: uuid.UUID) -> None:
        captured.append(application_id)

    monkeypatch.setattr(bulk_upload_service, "_enqueue_cv_pipeline", _fake_enqueue)
    return captured


# --- Auth gate ---------------------------------------------------------------


async def test_move_to_pool_requires_auth(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(f"/applications/{uuid.uuid4()}/move-to-pool")
    assert resp.status_code == 401


# --- Source deactivates the pool entry ---------------------------------------


async def test_source_deactivates_pool_entry(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    no_enqueue: list[uuid.UUID],
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    await _seed_cv_doc(db_session, candidate_id=cand.id)
    entry = await _seed_pool_entry(db_session, candidate_id=cand.id, added_by=user.id)

    resp = await client.post(
        "/talent-pool/source",
        json={"candidate_id": str(cand.id), "job_id": str(job.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["already_existed"] is False
    assert body["sourced_from_talent_pool"] is True

    # The candidate has left the pool and now lives only in the job pipeline.
    await db_session.refresh(entry)
    assert entry.is_active is False

    app = (
        await db_session.execute(
            select(Application).where(
                Application.candidate_id == cand.id, Application.job_id == job.id
            )
        )
    ).scalar_one()
    assert app.sourced_from_talent_pool is True


# --- Move back to pool archives the application ------------------------------


async def test_move_to_pool_archives_application_and_preserves_history(
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
        db_session, job_id=job.id, candidate_id=cand.id, cv_document_id=cv.id
    )
    # An append-only score row that must SURVIVE the archive as history.
    db_session.add(
        ApplicationScore(
            application_id=app.id,
            score_type=ScoreType.SIMILARITY,
            value=72.5,
            breakdown={},
            prompt_version="-",
            model_used=ScoreModel.OPENAI_EMBED_3_SMALL.value,
        )
    )
    await db_session.flush()
    original_stage = app.stage

    resp = await client.post(
        f"/applications/{app.id}/move-to-pool",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidate_id"] == str(cand.id)
    assert body["is_active"] is True

    # Application retained as an archived stint: status flips, stage is
    # preserved, archived_at is stamped, and its score row survives.
    await db_session.refresh(app)
    assert app.status is ApplicationStatus.ARCHIVED
    assert app.stage is original_stage
    assert app.archived_at is not None
    scores = (
        (
            await db_session.execute(
                select(ApplicationScore).where(ApplicationScore.application_id == app.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(scores) == 1
    pool = (
        await db_session.execute(
            select(TalentPoolEntry).where(TalentPoolEntry.candidate_id == cand.id)
        )
    ).scalar_one()
    assert pool.is_active is True


async def test_move_to_pool_stores_optional_reason_and_surfaces_in_history(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    """An optional reason is saved against the archived stint + shown in history."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    auth = {"Authorization": f"Bearer {token}"}
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session, job_id=job.id, candidate_id=cand.id, cv_document_id=cv.id
    )
    await db_session.flush()

    reason = "Strong profile but role was put on hold — keep warm for future reqs."
    resp = await client.post(
        f"/applications/{app.id}/move-to-pool",
        headers=auth,
        json={"reason": reason},
    )
    assert resp.status_code == 200, resp.text

    # Persisted against this specific application (the job stint).
    await db_session.refresh(app)
    assert app.move_to_pool_reason == reason

    # And surfaced per-stint in the candidate's cross-job history.
    r = await client.get(f"/talent-pool/candidates/{cand.id}/history", headers=auth)
    assert r.status_code == 200, r.text
    stint = r.json()["stints"][0]
    assert stint["move_to_pool_reason"] == reason


async def test_move_to_pool_without_reason_leaves_it_null(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    """A bare move (no body) still works and stores no reason."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    cv = await _seed_cv_doc(db_session, candidate_id=cand.id)
    app = await _seed_application(
        db_session, job_id=job.id, candidate_id=cand.id, cv_document_id=cv.id
    )
    await db_session.flush()

    resp = await client.post(
        f"/applications/{app.id}/move-to-pool",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    await db_session.refresh(app)
    assert app.move_to_pool_reason is None


async def test_move_to_pool_unknown_application_returns_404(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    resp = await client.post(
        f"/applications/{uuid.uuid4()}/move-to-pool",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# --- Full round-trip ---------------------------------------------------------


async def test_round_trip_source_move_resource(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    no_enqueue: list[uuid.UUID],
) -> None:
    """pool → source → move-to-pool → source again, with no duplicate error."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    auth = {"Authorization": f"Bearer {token}"}
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_candidate(db_session)
    await _seed_cv_doc(db_session, candidate_id=cand.id)
    await _seed_pool_entry(db_session, candidate_id=cand.id, added_by=user.id)

    # 1. Source onto the job.
    r1 = await client.post(
        "/talent-pool/source",
        json={"candidate_id": str(cand.id), "job_id": str(job.id)},
        headers=auth,
    )
    assert r1.status_code == 202, r1.text
    app_id = uuid.UUID(r1.json()["application_id"])

    # 2. Move back to the pool — the application is archived, not deleted.
    r2 = await client.post(f"/applications/{app_id}/move-to-pool", headers=auth)
    assert r2.status_code == 200, r2.text
    archived = await db_session.get(Application, app_id)
    assert archived is not None
    assert archived.status is ApplicationStatus.ARCHIVED

    # 3. Source again — a brand-new application, no "already in the job" error.
    #    The archived stint coexists with the fresh live one (live-uniqueness).
    r3 = await client.post(
        "/talent-pool/source",
        json={"candidate_id": str(cand.id), "job_id": str(job.id)},
        headers=auth,
    )
    assert r3.status_code == 202, r3.text
    assert r3.json()["already_existed"] is False
    new_app_id = uuid.UUID(r3.json()["application_id"])
    assert new_app_id != app_id

    # Both stints exist for the same (candidate, job): one archived, one live.
    both = (
        (
            await db_session.execute(
                select(Application).where(
                    Application.candidate_id == cand.id,
                    Application.job_id == job.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert {a.id for a in both} == {app_id, new_app_id}
    assert {a.status for a in both} == {
        ApplicationStatus.ARCHIVED,
        ApplicationStatus.ACTIVE,
    }


# --- Cross-job candidate history ---------------------------------------------


async def test_candidate_history_across_jobs_and_pool_round_trips(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    no_enqueue: list[uuid.UUID],
) -> None:
    """job A → pool → job B → pool: the pool shows the full scored history.

    Mirrors the user's flow exactly: a candidate bounces through two jobs and
    back to the pool each time. The history endpoint must surface both stints,
    each scored against its own job, plus candidate-level authenticity once.
    """
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    auth = {"Authorization": f"Bearer {token}"}

    job_a = await _seed_job(db_session, created_by=user.id)
    job_b = await _seed_job(db_session, created_by=user.id)
    job_b.title = "Data Scientist"
    cand = await _seed_candidate(db_session)
    await _seed_cv_doc(db_session, candidate_id=cand.id)
    # Candidate-level authenticity (shared across every stint).
    cand.authenticity_score = 88.0
    cand.authenticity_band = AuthenticityBand.AUTHENTIC
    cand.authenticity_computed_at = datetime.now(UTC)
    await _seed_pool_entry(db_session, candidate_id=cand.id, added_by=user.id)
    await db_session.flush()

    # --- Stint 1: source onto job A, score it, screen it, move back to pool ---
    r = await client.post(
        "/talent-pool/source",
        json={"candidate_id": str(cand.id), "job_id": str(job_a.id)},
        headers=auth,
    )
    assert r.status_code == 202, r.text
    app_a_id = uuid.UUID(r.json()["application_id"])
    db_session.add(
        ApplicationScore(
            application_id=app_a_id,
            score_type=ScoreType.SIMILARITY,
            value=71.0,
            breakdown={},
            prompt_version="-",
            model_used=ScoreModel.OPENAI_EMBED_3_SMALL.value,
        )
    )
    # A WhatsApp screening conversation with one scored answer.
    conv = WhatsAppConversation(
        application_id=app_a_id,
        candidate_id=cand.id,
        job_id=job_a.id,
        state=WhatsAppConversationState.COMPLETED,
        answers=[{"question_id": "q1", "question": "Years of Python?", "answer": "5 years"}],
        phone_e164=cand.phone_e164,
    )
    db_session.add(conv)
    await db_session.flush()
    db_session.add(
        WhatsAppMessage(
            conversation_id=conv.id,
            direction=WhatsAppDirection.INBOUND,
            message_type="text",
            body="5 years",
            question_index=0,
            answer_relevance_score=90,
            answer_ai_score=10,
            answer_score_rationale="Specific and on-topic.",
        )
    )
    await db_session.flush()
    r = await client.post(f"/applications/{app_a_id}/move-to-pool", headers=auth)
    assert r.status_code == 200, r.text

    # --- Stint 2: source onto job B, score it, move back to pool --------------
    r = await client.post(
        "/talent-pool/source",
        json={"candidate_id": str(cand.id), "job_id": str(job_b.id)},
        headers=auth,
    )
    assert r.status_code == 202, r.text
    app_b_id = uuid.UUID(r.json()["application_id"])
    db_session.add(
        ApplicationScore(
            application_id=app_b_id,
            score_type=ScoreType.HARD_FILTER,
            value=64.0,
            breakdown={},
            prompt_version="hard_filter:v1",
            model_used=ScoreModel.CLAUDE_OPUS.value,
        )
    )
    await db_session.flush()
    r = await client.post(f"/applications/{app_b_id}/move-to-pool", headers=auth)
    assert r.status_code == 200, r.text

    # --- The pool now shows the whole journey --------------------------------
    r = await client.get(f"/talent-pool/candidates/{cand.id}/history", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["total_stints"] == 2
    assert body["in_pool"] is True
    # Candidate-level authenticity surfaces once on the snapshot.
    assert body["candidate"]["authenticity_score"] == "88%"
    assert body["candidate"]["authenticity_band"] == "authentic"

    stints = {uuid.UUID(s["application_id"]): s for s in body["stints"]}
    assert set(stints) == {app_a_id, app_b_id}
    assert all(s["status"] == "archived" for s in stints.values())

    stint_a = stints[app_a_id]
    assert stint_a["job_title"] == "Senior Backend Engineer"
    assert stint_a["sourced_from_talent_pool"] is True
    assert stint_a["archived_at"] is not None
    assert {sc["score_type"] for sc in stint_a["scores"]} == {"similarity"}
    # The screening digest carries the scored answer.
    assert stint_a["screening"]["state"] == "completed"
    answer = stint_a["screening"]["answers"][0]
    assert answer["answer"] == "5 years"
    assert answer["relevance_score"] == 90
    assert answer["ai_score"] == 10

    stint_b = stints[app_b_id]
    assert stint_b["job_title"] == "Data Scientist"
    assert {sc["score_type"] for sc in stint_b["scores"]} == {"hard_filter"}
    assert stint_b["screening"] is None


async def test_candidate_history_unknown_candidate_returns_404(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    r = await client.get(
        f"/talent-pool/candidates/{uuid.uuid4()}/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


async def test_candidate_history_requires_auth(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    r = await client.get(f"/talent-pool/candidates/{uuid.uuid4()}/history")
    assert r.status_code == 401


# --- Search (text query + job-relevance) + response enrichment --------------

_RICH_PROFILE = {
    "work_history": [{"company": "Acme", "title": "Staff Engineer", "start": "2019", "end": None}],
    "skills": ["Python", "Go", "SQL"],
}


def _unit_vec(index: int, dim: int = 1536) -> list[float]:
    """1536-d unit vector with 1.0 at ``index`` (deterministic cosine ordering)."""
    return _vec(index, dim=dim)


def _vec(*indices: int, dim: int = 1536) -> list[float]:
    """1536-d vector with 1.0 at each given index.

    Two shared indices vs one give cosine ≈0.71 (a *relevant* but lower-ranked
    hit); disjoint indices give cosine 0 (filtered by the relevance gate).
    """
    vec = [0.0] * dim
    for i in indices:
        vec[i] = 1.0
    return vec


async def _seed_embedded_candidate(
    db_session: AsyncSession,
    *,
    added_by: uuid.UUID,
    embedding: list[float],
    full_name: str = "Test Candidate",
    parsed_profile: dict[str, object] | None = None,
    authenticity_score: float | None = None,
    authenticity_band: AuthenticityBand | None = None,
    source_job_id: uuid.UUID | None = None,
) -> Candidate:
    """A pooled candidate whose current CV carries ``embedding``."""
    cand = Candidate(
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        phone_e164=f"+9715{uuid.uuid4().hex[:7]}",
        full_name=full_name,
        parsed_profile=parsed_profile or {},
        authenticity_score=authenticity_score,
        authenticity_band=authenticity_band,
    )
    db_session.add(cand)
    await db_session.flush()
    db_session.add(
        CvDocument(
            candidate_id=cand.id,
            blob_url="https://blob.invalid/test.pdf",
            blob_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            is_current=True,
            cv_embedding=embedding,
        )
    )
    now = datetime.now(UTC)
    db_session.add(
        TalentPoolEntry(
            candidate_id=cand.id,
            source_job_id=source_job_id,
            added_by=added_by,
            added_at=now,
            expires_at=now + timedelta(days=30),
            is_active=True,
        )
    )
    await db_session.flush()
    return cand


async def test_search_by_job_ranks_by_jd_embedding(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    headers = {"Authorization": f"Bearer {token}"}

    job = await _seed_job(db_session, created_by=user.id)
    job.jd_embedding = _unit_vec(0)
    await db_session.flush()

    near = await _seed_embedded_candidate(
        db_session,
        added_by=user.id,
        embedding=_unit_vec(0),  # identical to the JD vector → top hit
        full_name="Near Match",
        parsed_profile=_RICH_PROFILE,
        authenticity_score=88.0,
        authenticity_band=AuthenticityBand.AUTHENTIC,
        source_job_id=job.id,
    )
    mid = await _seed_embedded_candidate(
        db_session, added_by=user.id, embedding=_vec(0, 1), full_name="Mid Match"
    )  # cosine ≈0.71 → relevant, ranks below near
    far = await _seed_embedded_candidate(
        db_session, added_by=user.id, embedding=_unit_vec(5), full_name="Far Match"
    )  # cosine 0 → below the relevance gate, excluded

    r = await client.get(f"/talent-pool/search?job_id={job.id}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"] == job.title  # echoes the job title, not free text

    ids = [i["candidate_id"] for i in body["items"]]
    # Relevant ones returned in rank order; the irrelevant one is filtered out.
    assert ids == [str(near.id), str(mid.id)]
    assert str(far.id) not in ids

    top = body["items"][0]
    assert top["similarity_score"] >= 99.0
    # Enriched fields surface on the candidate snapshot + entry.
    assert top["candidate"]["role"] == "Staff Engineer"
    assert "Python" in top["candidate"]["skills"]
    assert top["candidate"]["authenticity_score"] == 88.0
    assert top["candidate"]["authenticity_band"] == "authentic"
    assert top["source_job_title"] == job.title


async def test_search_by_text_matches_role_skills_name(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    """Free-text search is lexical: it matches role titles, skills, and name -
    not noisy embedding similarity. Hits carry no similarity_score."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    headers = {"Authorization": f"Bearer {token}"}

    swe = await _seed_embedded_candidate(
        db_session,
        added_by=user.id,
        embedding=_unit_vec(0),
        full_name="Sam Coder",
        parsed_profile={
            "work_history": [{"company": "X", "title": "Senior Software Engineer"}],
            "skills": ["Python", "Go"],
        },
    )
    qa = await _seed_embedded_candidate(
        db_session,
        added_by=user.id,
        embedding=_unit_vec(1),
        full_name="Quinn Tester",
        parsed_profile={
            "work_history": [{"company": "Y", "title": "QA Engineer"}],
            "skills": ["Selenium", "pytest"],
        },
    )
    fin = await _seed_embedded_candidate(
        db_session,
        added_by=user.id,
        embedding=_unit_vec(2),
        full_name="Fiona Money",
        parsed_profile={
            "work_history": [{"company": "Z", "title": "Finance Analyst"}],
            "skills": ["IFRS", "Excel"],
        },
    )

    # Role phrase → matches the software engineer by role title, never the QA or
    # finance person. (Membership, not equality: the shared dev DB may already
    # hold other genuine software engineers, which is correct to return too.)
    r = await client.get("/talent-pool/search", params={"q": "software engineer"}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"] == "software engineer"
    ids = {i["candidate_id"] for i in body["items"]}
    assert str(swe.id) in ids
    assert str(qa.id) not in ids
    assert str(fin.id) not in ids
    swe_hit = next(i for i in body["items"] if i["candidate_id"] == str(swe.id))
    assert swe_hit["similarity_score"] is None  # lexical hit: no score

    # Skill term → matches on the skills list, not the role.
    r2 = await client.get("/talent-pool/search", params={"q": "selenium"}, headers=headers)
    skill_ids = {i["candidate_id"] for i in r2.json()["items"]}
    assert str(qa.id) in skill_ids
    assert str(swe.id) not in skill_ids
    assert str(fin.id) not in skill_ids

    # Name term → matched on full_name.
    r3 = await client.get("/talent-pool/search", params={"q": "fiona money"}, headers=headers)
    name_ids = {i["candidate_id"] for i in r3.json()["items"]}
    assert str(fin.id) in name_ids
    assert str(swe.id) not in name_ids


async def test_search_relevance_gate_returns_empty_when_nothing_relevant(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    """A pool of only-irrelevant candidates yields no hits (not a ranked list)."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    headers = {"Authorization": f"Bearer {token}"}

    job = await _seed_job(db_session, created_by=user.id)
    job.jd_embedding = _unit_vec(0)
    await db_session.flush()
    # Every candidate is orthogonal to the JD → all below the relevance gate.
    for idx in (3, 4, 5):
        await _seed_embedded_candidate(db_session, added_by=user.id, embedding=_unit_vec(idx))

    r = await client.get(f"/talent-pool/search?job_id={job.id}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_search_requires_q_or_job_id(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    r = await client.get("/talent-pool/search", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 422
    assert r.json()["error"] == "search_query_required"


async def test_search_by_unknown_job_returns_404(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    r = await client.get(
        f"/talent-pool/search?job_id={uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
    assert r.json()["error"] == "job_not_found"


async def test_search_by_job_without_embedding_returns_409(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)  # jd_embedding is None
    r = await client.get(
        f"/talent-pool/search?job_id={job.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 409
    assert r.json()["error"] == "job_not_embedded"


async def test_list_enriches_role_skills_source_and_authenticity(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    job = await _seed_job(db_session, created_by=user.id)
    cand = await _seed_embedded_candidate(
        db_session,
        added_by=user.id,
        embedding=_unit_vec(0),
        full_name="Enriched Cand",
        parsed_profile=_RICH_PROFILE,
        authenticity_score=72.5,
        authenticity_band=AuthenticityBand.REVIEW,
        source_job_id=job.id,
    )

    r = await client.get("/talent-pool", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    item = next(i for i in r.json()["items"] if i["candidate_id"] == str(cand.id))
    assert item["source_job_title"] == job.title
    assert item["candidate"]["role"] == "Staff Engineer"
    assert item["candidate"]["skills"][:3] == ["Python", "Go", "SQL"]
    assert item["candidate"]["authenticity_score"] == 72.5
    assert item["candidate"]["authenticity_band"] == "review"
