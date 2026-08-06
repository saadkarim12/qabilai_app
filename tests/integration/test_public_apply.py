"""Integration tests for the public CV upload endpoint (Step 3.8).

Strategy
--------

* The Azure Blob upload and the Celery dispatcher are patched at the
  intake-service level so the tests neither touch Azure nor enqueue
  real broker work.
* The Redis-backed slug cache is also patched — the per-test session
  transaction can't see uncommitted writes through Redis anyway, so we
  short-circuit the cache lookup entirely.
* All other paths run end-to-end against the real DB inside the per-test
  rollback transaction (see ``tests/integration/conftest.py``).

Doc Step 3.8 acceptance criteria covered:

* Happy path returns 202 with a hex reference number; application,
  candidate, and cv_document rows are present afterwards.
* Duplicate sha256 (same bytes re-uploaded) doesn't create a second
  cv_document; the application row is unchanged.
* Inactive slug returns 410.
* Missing consent returns 400.
* Same candidate applying twice to the same job returns 202 with the
  same reference number (idempotent on (candidate_id, job_id)).
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
from src.db.models.public_upload_token import PublicUploadToken
from src.db.models.user import User
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.jobs import (
    EmploymentType,
    JobStatus,
    NoticePeriod,
    VisaRequirement,
    WorkMode,
)
from src.enums.roles import UserRole
from src.integrations.azure_blob import UploadedBlob
from src.services import application_intake_service as intake_module
from src.utils.password import hash_password
from src.utils.slug import generate_public_slug

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test data + monkeypatch helpers
# ---------------------------------------------------------------------------


PDF_HEADER_BYTES = b"%PDF-1.4\n"
PDF_BODY_PAD = b"0123456789" * 100  # ~1 KB so size validation is happy

VALID_PDF_BYTES = PDF_HEADER_BYTES + PDF_BODY_PAD + b"\n%%EOF\n"
DIFFERENT_VALID_PDF_BYTES = PDF_HEADER_BYTES + PDF_BODY_PAD + b"\nDIFF\n%%EOF\n"


class _FakeRedis:
    """Tiny in-memory stand-in for the slug cache.

    Mirrors only the two methods the intake service uses: ``get``,
    ``setex``. Per-test instance so different tests don't bleed cached
    slug lookups into each other.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self._store[key] = value


def _patch_intake(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub out Redis + blob upload + Celery dispatch.

    Returns the list of dispatched application_ids so tests can assert
    on which applications got enqueued.
    """
    fake_redis = _FakeRedis()
    monkeypatch.setattr(intake_module, "_redis_client", lambda: fake_redis)

    async def _fake_upload(pdf_bytes: bytes, *, candidate_id: uuid.UUID) -> UploadedBlob:
        # Compute the same sha256 the real path would (so dedup logic
        # exercises the real comparison) and produce a deterministic URL.
        import hashlib

        digest = hashlib.sha256(pdf_bytes).hexdigest()
        return UploadedBlob(
            path=f"cvs/{candidate_id}/{digest}.pdf",
            url=f"https://test.blob/cvs/{candidate_id}/{digest}.pdf",
            sha256=digest,
        )

    monkeypatch.setattr(intake_module, "upload_pdf", _fake_upload)

    dispatched: list[str] = []

    def _fake_enqueue(application_id: uuid.UUID) -> None:
        dispatched.append(str(application_id))

    monkeypatch.setattr(intake_module, "_enqueue_cv_pipeline", _fake_enqueue)
    return dispatched


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


async def _seed_job_with_slug(
    db_session: AsyncSession, *, slug_is_active: bool = True
) -> tuple[Job, PublicUploadToken]:
    user = await _seed_admin(db_session)
    slug = generate_public_slug()
    job = Job(
        created_by=user.id,
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
        visa_requirement=VisaRequirement.SPONSORSHIP_OFFERED,
        nationality_preference=[],
        languages_required=["english", "arabic"],
        ai_verify_flags={},
        job_description="Build the Kabil hiring backend. Strong async Python.",
        status=JobStatus.OPEN,
        public_slug=slug,
        whatsapp_questions=[],
        pipeline_status={},
        ready_for_applications=True,
    )
    db_session.add(job)
    await db_session.flush()
    token = PublicUploadToken(job_id=job.id, slug=slug, is_active=slug_is_active)
    db_session.add(token)
    await db_session.flush()
    return job, token


def _form_data(
    *,
    email: str = "candidate@example.com",
    phone: str = "+971501234567",
    full_name: str = "Sample Candidate",
    consent: str = "true",
    honeypot: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "email": email,
        "phone": phone,
        "full_name": full_name,
        "consent": consent,
    }
    if honeypot is not None:
        data["honeypot"] = honeypot
    return data


def _files(pdf_bytes: bytes = VALID_PDF_BYTES) -> dict[str, Any]:
    return {"pdf": ("cv.pdf", pdf_bytes, "application/pdf")}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_happy_path_returns_202_and_creates_rows(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc acceptance: 'Happy path: returns 202 with ref number, Celery chain enqueued'."""
    dispatched = _patch_intake(monkeypatch)
    job, token = await _seed_job_with_slug(db_session)

    resp = await client.post(
        f"/public/apply/{token.slug}/upload",
        data=_form_data(),
        files=_files(),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "reference_number" in body
    assert len(body["reference_number"]) == 32  # UUID hex
    # Returned ref number is the Application id; verify the row exists.
    app_id = uuid.UUID(body["reference_number"])

    application = await db_session.get(Application, app_id)
    assert application is not None
    assert application.job_id == job.id
    assert application.stage is ApplicationStage.VECTOR_SCREEN
    assert application.status is ApplicationStatus.ACTIVE
    assert application.consent_context["ip"]
    assert application.consent_context["user_agent"]

    candidate = await db_session.get(Candidate, application.candidate_id)
    assert candidate is not None
    assert candidate.email == "candidate@example.com"
    assert candidate.phone_e164 == "+971501234567"
    assert candidate.full_name == "Sample Candidate"

    cv_doc = await db_session.get(CvDocument, application.cv_document_id)
    assert cv_doc is not None
    assert cv_doc.is_current is True
    assert cv_doc.blob_sha256

    # CV pipeline dispatch was called exactly once for this application.
    assert dispatched == [str(app_id)]


# ---------------------------------------------------------------------------
# Duplicate sha256 — same bytes uploaded again
# ---------------------------------------------------------------------------


async def test_duplicate_sha256_does_not_create_second_cv_document(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc acceptance: 'Duplicate sha256 PDF: doesn't re-upload, doesn't crash'."""
    _patch_intake(monkeypatch)
    _, token = await _seed_job_with_slug(db_session)

    # First upload creates everything.
    first = await client.post(
        f"/public/apply/{token.slug}/upload",
        data=_form_data(),
        files=_files(),
    )
    assert first.status_code == 202, first.text

    # Second upload with identical bytes — same candidate, same job.
    second = await client.post(
        f"/public/apply/{token.slug}/upload",
        data=_form_data(),
        files=_files(),
    )
    assert second.status_code == 202, second.text

    # Reference number is the same (idempotent on candidate+job).
    assert first.json()["reference_number"] == second.json()["reference_number"]

    # Exactly one CvDocument row for this candidate.
    candidate_row = (
        await db_session.execute(
            sa.select(Candidate).where(Candidate.email == "candidate@example.com")
        )
    ).scalar_one()
    cv_count = (
        await db_session.execute(
            sa.select(sa.func.count())
            .select_from(CvDocument)
            .where(CvDocument.candidate_id == candidate_row.id)
        )
    ).scalar_one()
    assert cv_count == 1


# ---------------------------------------------------------------------------
# Different sha256 (new bytes) — new cv_document, prior demoted
# ---------------------------------------------------------------------------


async def test_new_cv_bytes_create_new_cv_document_and_demote_old(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_intake(monkeypatch)
    _, token = await _seed_job_with_slug(db_session)

    # First upload.
    first = await client.post(
        f"/public/apply/{token.slug}/upload",
        data=_form_data(),
        files=_files(VALID_PDF_BYTES),
    )
    assert first.status_code == 202, first.text

    # Second upload with DIFFERENT PDF bytes (same candidate identity).
    second = await client.post(
        f"/public/apply/{token.slug}/upload",
        data=_form_data(),
        files=_files(DIFFERENT_VALID_PDF_BYTES),
    )
    assert second.status_code == 202, second.text

    # Same candidate ⇒ same application ref number (still idempotent on
    # candidate+job).
    assert first.json()["reference_number"] == second.json()["reference_number"]

    # Two CvDocument rows for this candidate; exactly one is current.
    candidate_row = (
        await db_session.execute(
            sa.select(Candidate).where(Candidate.email == "candidate@example.com")
        )
    ).scalar_one()
    cvs = (
        (
            await db_session.execute(
                sa.select(CvDocument).where(CvDocument.candidate_id == candidate_row.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(cvs) == 2
    current = [c for c in cvs if c.is_current]
    assert len(current) == 1


# ---------------------------------------------------------------------------
# Inactive slug → 410
# ---------------------------------------------------------------------------


async def test_inactive_slug_returns_410(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc acceptance: 'Inactive slug: returns 410 Gone'."""
    _patch_intake(monkeypatch)
    _, token = await _seed_job_with_slug(db_session, slug_is_active=False)

    resp = await client.post(
        f"/public/apply/{token.slug}/upload",
        data=_form_data(),
        files=_files(),
    )
    assert resp.status_code == 410, resp.text
    assert resp.json()["error"] == "slug_inactive_or_unknown"


async def test_unknown_slug_returns_410(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown slug → same 410 as inactive (no info leak)."""
    _patch_intake(monkeypatch)
    bogus_slug = generate_public_slug()
    resp = await client.post(
        f"/public/apply/{bogus_slug}/upload",
        data=_form_data(),
        files=_files(),
    )
    assert resp.status_code == 410, resp.text
    assert resp.json()["error"] == "slug_inactive_or_unknown"


# ---------------------------------------------------------------------------
# Missing consent → 400
# ---------------------------------------------------------------------------


async def test_consent_false_returns_400(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc acceptance: 'Missing consent: returns 400'."""
    _patch_intake(monkeypatch)
    _, token = await _seed_job_with_slug(db_session)

    resp = await client.post(
        f"/public/apply/{token.slug}/upload",
        data=_form_data(consent="false"),
        files=_files(),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "consent_required"


# ---------------------------------------------------------------------------
# Honeypot
# ---------------------------------------------------------------------------


async def test_honeypot_filled_returns_202_with_no_db_writes(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Honeypot triggered → fake 202, no candidate or application row."""
    dispatched = _patch_intake(monkeypatch)
    _, token = await _seed_job_with_slug(db_session)

    resp = await client.post(
        f"/public/apply/{token.slug}/upload",
        data=_form_data(honeypot="bot-was-here"),
        files=_files(),
    )
    assert resp.status_code == 202, resp.text
    # No application or candidate written.
    candidate_count = (
        await db_session.execute(sa.select(sa.func.count()).select_from(Candidate))
    ).scalar_one()
    application_count = (
        await db_session.execute(sa.select(sa.func.count()).select_from(Application))
    ).scalar_one()
    assert candidate_count == 0
    assert application_count == 0
    assert dispatched == []  # no enqueue


# ---------------------------------------------------------------------------
# Invalid email / phone
# ---------------------------------------------------------------------------


async def test_invalid_email_returns_400(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_intake(monkeypatch)
    _, token = await _seed_job_with_slug(db_session)

    resp = await client.post(
        f"/public/apply/{token.slug}/upload",
        data=_form_data(email="notanemail"),
        files=_files(),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "invalid_email"


async def test_invalid_phone_returns_400(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_intake(monkeypatch)
    _, token = await _seed_job_with_slug(db_session)

    resp = await client.post(
        f"/public/apply/{token.slug}/upload",
        data=_form_data(phone="not-a-phone"),
        files=_files(),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "invalid_phone"


# ---------------------------------------------------------------------------
# Schema sanity
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GET /public/apply/{slug} — candidate-facing job view
# ---------------------------------------------------------------------------


async def test_get_public_job_returns_trimmed_view(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    """Active slug resolves to a 200 with only candidate-facing fields."""
    job, token = await _seed_job_with_slug(db_session)

    resp = await client.get(f"/public/apply/{token.slug}")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Candidate-facing fields are present and correct.
    assert body["title"] == job.title
    assert body["hiring_company"] == job.hiring_company
    assert body["country"] == job.country
    assert body["city"] == job.city
    assert body["required_skills"] == job.required_skills
    assert body["job_description"] == job.job_description
    assert body["status"] == JobStatus.OPEN.value

    # Internal / HR fields must NOT leak.
    for leaked in (
        "id",
        "created_by",
        "public_slug",
        "whatsapp_questions",
        "pipeline_status",
        "ready_for_applications",
        "jd_embedding",
        "ai_verify_flags",
        "created_at",
        "updated_at",
        "closed_at",
    ):
        assert leaked not in body, f"{leaked!r} leaked into public job view"


async def test_get_public_job_closed_role_still_resolves(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    """A closed job with an active slug still resolves (status reflects it)."""
    job, token = await _seed_job_with_slug(db_session)
    job.status = JobStatus.CLOSED
    await db_session.flush()

    resp = await client.get(f"/public/apply/{token.slug}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == JobStatus.CLOSED.value


async def test_get_public_job_inactive_slug_returns_410(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    """Deactivated slug → 410, same as the upload route."""
    _, token = await _seed_job_with_slug(db_session, slug_is_active=False)

    resp = await client.get(f"/public/apply/{token.slug}")
    assert resp.status_code == 410, resp.text
    assert resp.json()["error"] == "slug_inactive_or_unknown"


async def test_get_public_job_unknown_slug_returns_410(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    """Unknown slug → same 410 as inactive (no info leak)."""
    resp = await client.get(f"/public/apply/{generate_public_slug()}")
    assert resp.status_code == 410, resp.text
    assert resp.json()["error"] == "slug_inactive_or_unknown"


async def test_consent_context_persists_ip_user_agent_and_accepted_at(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc security: 'Consent record stored with IP + user agent'."""
    _patch_intake(monkeypatch)
    _, token = await _seed_job_with_slug(db_session)

    resp = await client.post(
        f"/public/apply/{token.slug}/upload",
        data=_form_data(),
        files=_files(),
        headers={"User-Agent": "kabil-test-suite/1.0"},
    )
    assert resp.status_code == 202, resp.text
    app_id = uuid.UUID(resp.json()["reference_number"])
    application = await db_session.get(Application, app_id)
    assert application is not None
    assert application.consent_context["user_agent"] == "kabil-test-suite/1.0"
    assert application.consent_context["ip"]
    assert application.consent_context["accepted_at"]
    # consented_at scalar mirrors the JSONB value within a small window.
    parsed_accepted = datetime.fromisoformat(application.consent_context["accepted_at"])
    delta = abs((application.consented_at - parsed_accepted).total_seconds())
    assert delta < 1.0
    # Both timestamps are within ~5s of "now".
    assert abs((datetime.now(UTC) - application.consented_at).total_seconds()) < 5.0
