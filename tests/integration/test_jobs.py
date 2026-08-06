"""Integration tests for the /jobs/* routes.

Every test exercises the real FastAPI app via the shared ``client``
fixture; the DB session is the transactional one in conftest, so writes
are rolled back on teardown. Redis is stubbed via dependency injection
of ``job_service._redis_client`` so we don't need a live Redis to test
the route layer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.application import Application
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.job import Job
from src.db.models.user import User
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.jobs import JobStatus
from src.enums.roles import UserRole
from src.schemas.job import JdDraft
from src.services import job_service
from src.utils.password import hash_password

pytestmark = pytest.mark.integration


# --- Fixtures / helpers ------------------------------------------------------


class _FakeRedis:
    """In-memory async-compatible stand-in for the parts of redis we touch."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.get_calls = 0
        self.set_calls = 0

    async def get(self, key: str) -> bytes | None:
        self.get_calls += 1
        return self.store.get(key)

    async def set(self, key: str, value: str | bytes, ex: int | None = None) -> None:
        self.set_calls += 1
        self.store[key] = value.encode() if isinstance(value, str) else value


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """Replace ``job_service._redis_client`` with an in-memory stub."""
    fake = _FakeRedis()
    monkeypatch.setattr(job_service, "_redis_client", lambda: fake)
    return fake


async def _seed_user(
    db_session: AsyncSession, password: str = "hunter2-correct"
) -> tuple[User, str]:
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


def _job_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "Senior Backend Engineer",
        "hiring_company": "Kabil Test Co",
        "country": "AE",
        "city": "Dubai",
        "employment_type": "permanent",
        "work_mode": "hybrid",
        "currency": "AED",
        "min_salary": 20_000,
        "max_salary": 30_000,
        "notice_period": "30d",
        "min_experience_years": 5,
        "required_skills": ["Python", "PostgreSQL"],
        "preferred_skills": ["AWS"],
        "visa_requirement": "sponsorship_offered",
        "nationality_preference": [],
        "languages_required": ["english", "arabic"],
        "job_description": "Build the Kabil hiring backend. Strong async Python.",
    }
    base.update(overrides)
    return base


async def _create_job(client: httpx.AsyncClient, token: str, **overrides: Any) -> dict[str, Any]:
    resp = await client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {token}"},
        json=_job_payload(**overrides),
    )
    assert resp.status_code == 202, resp.text
    return dict(resp.json())


# --- Auth --------------------------------------------------------------------


async def test_create_requires_auth(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post("/jobs", json=_job_payload())
    assert resp.status_code == 401


async def test_list_requires_auth(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get("/jobs")
    assert resp.status_code == 401


# --- Create ------------------------------------------------------------------


async def test_create_job_returns_202_and_id(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    body = await _create_job(client, token)
    job_id = uuid.UUID(body["id"])

    row = await db_session.get(Job, job_id)
    assert row is not None
    assert row.status is JobStatus.DRAFT
    assert row.created_by == user.id
    assert row.public_slug
    assert len(row.public_slug) == 16

    # A matching active PublicUploadToken is created in the same transaction
    # so the public /apply/{slug}/upload route resolves the slug. Without
    # this row the public-apply endpoint returns 410 even though the job
    # exists.
    from src.db.models.public_upload_token import PublicUploadToken

    token_row = (
        await db_session.execute(
            sa.select(PublicUploadToken).where(PublicUploadToken.job_id == job_id)
        )
    ).scalar_one()
    assert token_row.slug == row.public_slug
    assert token_row.is_active is True


async def test_create_strips_html_from_description(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    body = await _create_job(
        client,
        token,
        job_description='Real text <script>alert("x")</script> after',
    )
    row = await db_session.get(Job, uuid.UUID(body["id"]))
    assert row is not None
    assert "<script>" not in row.job_description
    assert "Real text" in row.job_description
    assert "after" in row.job_description


async def test_create_rejects_invalid_country(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    resp = await client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {token}"},
        json=_job_payload(country="ZZ"),
    )
    assert resp.status_code == 422


async def test_create_rejects_inverted_salary_band(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    resp = await client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {token}"},
        json=_job_payload(min_salary=50_000, max_salary=20_000),
    )
    assert resp.status_code == 422


async def test_create_normalises_country_to_upper(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    body = await _create_job(client, token, country="ae")
    row = await db_session.get(Job, uuid.UUID(body["id"]))
    assert row is not None
    assert row.country == "AE"


# --- List --------------------------------------------------------------------


async def test_list_returns_items_and_pagination(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    for i in range(3):
        await _create_job(client, token, title=f"Engineer {i}")

    resp = await client.get(
        "/jobs?page=1&page_size=2",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["total"] >= 3
    assert len(body["items"]) == 2


async def test_list_items_carry_zero_filled_stage_funnel(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    created = await _create_job(client, token, title="Funnel job")
    created_id = created["id"]

    resp = await client.get(
        "/jobs?page=1&page_size=100",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    expected_stages = {s.value for s in ApplicationStage}

    # Every item carries the full zero-filled funnel contract.
    for item in items:
        funnel = item["applications_by_stage"]
        assert set(funnel) == expected_stages
        assert all(isinstance(count, int) and count >= 0 for count in funnel.values())

    # The freshly created job has no applications yet → all zeros.
    funnel = next(i["applications_by_stage"] for i in items if i["id"] == created_id)
    assert all(count == 0 for count in funnel.values())


async def test_list_funnel_excludes_archived_applications(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    """A moved-to-pool (archived) application drops out of the funnel.

    The jobs-list funnel must match the active pipeline count: archiving a
    candidate to the talent pool soft-archives their application, and those
    pooled candidates are no longer in the job's pipeline.
    """
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    created = await _create_job(client, token, title="Archive funnel job")
    job_id = uuid.UUID(created["id"])

    # Two applications at the same stage: one active, one archived (pooled).
    for status in (ApplicationStatus.ACTIVE, ApplicationStatus.ARCHIVED):
        cand = Candidate(
            email=f"{uuid.uuid4().hex[:8]}@example.com",
            phone_e164=f"+9715{uuid.uuid4().hex[:7]}",
            full_name="Funnel Candidate",
        )
        db_session.add(cand)
        await db_session.flush()
        cv = CvDocument(
            candidate_id=cand.id,
            blob_url="https://blob.invalid/test.pdf",
            blob_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            is_current=True,
        )
        db_session.add(cv)
        await db_session.flush()
        db_session.add(
            Application(
                job_id=job_id,
                candidate_id=cand.id,
                cv_document_id=cv.id,
                stage=ApplicationStage.VECTOR_SCREEN,
                status=status,
                consent_context={"ip": "127.0.0.1", "user_agent": "pytest"},
                consented_at=datetime.now(UTC),
            )
        )
    await db_session.flush()

    resp = await client.get(
        "/jobs?page=1&page_size=100",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    funnel = next(i["applications_by_stage"] for i in items if i["id"] == str(job_id))

    # Only the active application is counted; the archived one is excluded.
    assert funnel[ApplicationStage.VECTOR_SCREEN.value] == 1
    assert sum(funnel.values()) == 1


async def test_list_status_filter_excludes_other_statuses(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    draft_body = await _create_job(client, token, title="Draft only")
    draft_id = draft_body["id"]

    resp = await client.get(
        "/jobs?status=draft",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["items"]]
    assert draft_id in ids

    resp = await client.get(
        "/jobs?status=closed",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert draft_id not in [item["id"] for item in resp.json()["items"]]


async def test_list_search_is_case_insensitive_substring(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    unique = uuid.uuid4().hex[:8]
    await _create_job(client, token, title=f"Vivid {unique} Architect")

    resp = await client.get(
        f"/jobs?search={unique.upper()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert unique in items[0]["title"]


async def test_list_order_title_is_case_insensitive_alphabetical(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    """``order=title`` sorts alphabetically, ignoring case.

    Created out of order (and with mixed case) so a naive/no-sort or a
    case-sensitive sort would fail. Scoped to this test's jobs via a unique
    search token so the assertion is isolated from any other rows.
    """
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    tok = uuid.uuid4().hex[:8]
    # Insert in non-alphabetical order; expected order is apple, Banana, cherry.
    for title in (f"{tok} Banana", f"{tok} cherry", f"{tok} apple"):
        await _create_job(client, token, title=title)

    resp = await client.get(
        f"/jobs?search={tok}&order=title",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    titles = [i["title"] for i in resp.json()["items"]]
    assert titles == [f"{tok} apple", f"{tok} Banana", f"{tok} cherry"]


# --- Detail ------------------------------------------------------------------


async def test_detail_returns_full_payload(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    created = await _create_job(client, token)
    resp = await client.get(
        f"/jobs/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created["id"]
    assert body["status"] == "draft"
    assert body["whatsapp_questions"] == []
    # funnel_counts is intentionally absent — applications table arrives in Phase 3.
    assert "funnel_counts" not in body


async def test_detail_cache_hit_on_second_read(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    created = await _create_job(client, token)

    headers = {"Authorization": f"Bearer {token}"}
    fake_redis.get_calls = 0
    fake_redis.set_calls = 0

    # First GET → cache miss, populates Redis.
    r1 = await client.get(f"/jobs/{created['id']}", headers=headers)
    assert r1.status_code == 200
    assert fake_redis.set_calls == 1

    # Second GET → cache hit, no new SET.
    r2 = await client.get(f"/jobs/{created['id']}", headers=headers)
    assert r2.status_code == 200
    assert fake_redis.set_calls == 1
    assert r1.json() == r2.json()


async def test_detail_returns_404_for_unknown_id(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    bogus = uuid.uuid4()
    resp = await client.get(f"/jobs/{bogus}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404
    assert resp.json()["error"] == "job_not_found"


# --- Status PATCH ------------------------------------------------------------


async def test_status_draft_to_open_to_closed(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    headers = {"Authorization": f"Bearer {token}"}

    created = await _create_job(client, token)
    job_id = created["id"]

    r1 = await client.patch(f"/jobs/{job_id}/status", headers=headers, json={"status": "open"})
    assert r1.status_code == 200
    assert r1.json()["status"] == "open"

    r2 = await client.patch(f"/jobs/{job_id}/status", headers=headers, json={"status": "closed"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "closed"
    assert r2.json()["closed_at"] is not None


async def test_status_illegal_transition_409(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    headers = {"Authorization": f"Bearer {token}"}

    created = await _create_job(client, token)
    # draft → closed is not allowed.
    resp = await client.patch(
        f"/jobs/{created['id']}/status", headers=headers, json={"status": "closed"}
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "illegal_status_transition"


async def test_status_rejects_draft_in_body(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    headers = {"Authorization": f"Bearer {token}"}
    created = await _create_job(client, token)
    resp = await client.patch(
        f"/jobs/{created['id']}/status", headers=headers, json={"status": "draft"}
    )
    assert resp.status_code == 422


async def test_status_open_inactive_open_cycle(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    """Active → paused (inactive) → reactivated. Pausing leaves closed_at unset."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    headers = {"Authorization": f"Bearer {token}"}
    job_id = (await _create_job(client, token))["id"]

    await client.patch(f"/jobs/{job_id}/status", headers=headers, json={"status": "open"})

    r_inactive = await client.patch(
        f"/jobs/{job_id}/status", headers=headers, json={"status": "inactive"}
    )
    assert r_inactive.status_code == 200
    assert r_inactive.json()["status"] == "inactive"
    assert r_inactive.json()["closed_at"] is None  # a pause is not an end

    r_reopen = await client.patch(
        f"/jobs/{job_id}/status", headers=headers, json={"status": "open"}
    )
    assert r_reopen.status_code == 200
    assert r_reopen.json()["status"] == "open"


async def test_status_archive_stamps_closed_at_and_reactivates(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    """Archiving stamps closed_at; reactivating to open clears it again."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    headers = {"Authorization": f"Bearer {token}"}
    job_id = (await _create_job(client, token))["id"]

    await client.patch(f"/jobs/{job_id}/status", headers=headers, json={"status": "open"})

    r_archive = await client.patch(
        f"/jobs/{job_id}/status", headers=headers, json={"status": "archived"}
    )
    assert r_archive.status_code == 200
    assert r_archive.json()["status"] == "archived"
    assert r_archive.json()["closed_at"] is not None

    r_reactivate = await client.patch(
        f"/jobs/{job_id}/status", headers=headers, json={"status": "open"}
    )
    assert r_reactivate.status_code == 200
    assert r_reactivate.json()["status"] == "open"
    assert r_reactivate.json()["closed_at"] is None


async def test_status_inactive_to_closed_is_illegal(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    """inactive → closed is not a permitted edge (archive is the terminal path)."""
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    headers = {"Authorization": f"Bearer {token}"}
    job_id = (await _create_job(client, token))["id"]

    await client.patch(f"/jobs/{job_id}/status", headers=headers, json={"status": "open"})
    await client.patch(f"/jobs/{job_id}/status", headers=headers, json={"status": "inactive"})

    resp = await client.patch(f"/jobs/{job_id}/status", headers=headers, json={"status": "closed"})
    assert resp.status_code == 409
    assert resp.json()["error"] == "illegal_status_transition"


# --- WhatsApp questions ------------------------------------------------------


def _question(idx: int) -> dict[str, Any]:
    return {
        "id": f"q_id{idx:06d}",
        "order": idx,
        "category": "background_validation",
        "subcategory": "Experience",
        "question_en": "How many years of Python?",
        "question_ar": "كم سنة من خبرة بايثون؟",
        "reasoning": "Validates min_experience_years claim.",
        "is_ai_generated": True,
        "source_field": None,
    }


async def test_whatsapp_questions_get_empty_then_patch_then_get(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    headers = {"Authorization": f"Bearer {token}"}
    created = await _create_job(client, token)

    r1 = await client.get(f"/jobs/{created['id']}/whatsapp-questions", headers=headers)
    assert r1.status_code == 200
    assert r1.json() == {"questions": []}

    payload = {"questions": [_question(1), _question(2)]}
    r2 = await client.patch(
        f"/jobs/{created['id']}/whatsapp-questions",
        headers=headers,
        json=payload,
    )
    assert r2.status_code == 200
    assert len(r2.json()["questions"]) == 2

    # Round-trip from DB JSONB to confirm persistence.
    await db_session.execute(sa.text("SELECT 1"))
    row = await db_session.get(Job, uuid.UUID(created["id"]))
    assert row is not None
    await db_session.refresh(row)
    assert len(row.whatsapp_questions) == 2


async def test_whatsapp_questions_patch_rejects_duplicate_ids(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    headers = {"Authorization": f"Bearer {token}"}
    created = await _create_job(client, token)

    q1 = _question(1)
    q2 = _question(2)
    q2["id"] = q1["id"]  # collision

    resp = await client.patch(
        f"/jobs/{created['id']}/whatsapp-questions",
        headers=headers,
        json={"questions": [q1, q2]},
    )
    assert resp.status_code == 422


async def test_whatsapp_questions_patch_rejects_missing_arabic(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    headers = {"Authorization": f"Bearer {token}"}
    created = await _create_job(client, token)

    bad = _question(1)
    bad["question_ar"] = ""

    resp = await client.patch(
        f"/jobs/{created['id']}/whatsapp-questions",
        headers=headers,
        json={"questions": [bad]},
    )
    assert resp.status_code == 422


async def test_whatsapp_questions_patch_caps_at_max(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    headers = {"Authorization": f"Bearer {token}"}
    created = await _create_job(client, token)

    # MAX_WHATSAPP_QUESTIONS is 15; 16 questions must be rejected.
    payload = {"questions": [_question(i) for i in range(1, 17)]}
    resp = await client.patch(
        f"/jobs/{created['id']}/whatsapp-questions",
        headers=headers,
        json=payload,
    )
    assert resp.status_code == 422


async def test_unknown_job_id_for_whatsapp_questions_404(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    fake_redis: _FakeRedis,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    headers = {"Authorization": f"Bearer {token}"}
    bogus = uuid.uuid4()

    resp = await client.get(f"/jobs/{bogus}/whatsapp-questions", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["error"] == "job_not_found"


# --- AI JD builder (POST /jobs/generate-description) -------------------------


def _generate_payload(**overrides: Any) -> dict[str, Any]:
    """Role-Basics spec only — no job_description."""
    base = _job_payload(**overrides)
    base.pop("job_description", None)
    return base


async def test_generate_description_requires_auth(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post("/jobs/generate-description", json=_generate_payload())
    assert resp.status_code == 401


async def test_generate_description_returns_draft(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_generate(*, spec: Any, regenerate: bool = False) -> JdDraft:
        captured["title"] = spec.title
        captured["regenerate"] = regenerate
        return JdDraft(job_description_en="A" * 200, job_description_ar="ع" * 200)

    monkeypatch.setattr("src.api.routes.jobs.generate_job_description", fake_generate)

    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    resp = await client.post(
        "/jobs/generate-description",
        headers={"Authorization": f"Bearer {token}"},
        json=_generate_payload(title="Data Analyst"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["job_description"] == "A" * 200
    assert body["job_description_ar"] == "ع" * 200
    assert captured["title"] == "Data Analyst"
    assert captured["regenerate"] is False


async def test_generate_description_passes_regenerate_flag(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_generate(*, spec: Any, regenerate: bool = False) -> JdDraft:
        captured["regenerate"] = regenerate
        return JdDraft(job_description_en="B" * 200, job_description_ar="ب" * 200)

    monkeypatch.setattr("src.api.routes.jobs.generate_job_description", fake_generate)

    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    resp = await client.post(
        "/jobs/generate-description?regenerate=true",
        headers={"Authorization": f"Bearer {token}"},
        json=_generate_payload(),
    )
    assert resp.status_code == 200, resp.text
    assert captured["regenerate"] is True


async def test_generate_description_rejects_extra_field(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    # job_description is not part of the generate request (extra="forbid").
    payload = _generate_payload()
    payload["job_description"] = "should not be accepted here"

    resp = await client.post(
        "/jobs/generate-description",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert resp.status_code == 422
