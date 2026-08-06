"""Integration tests for the HR bulk-CV-upload endpoint.

Strategy
--------

* :mod:`src.services.bulk_upload_service` is patched in several places:
  - ``upload_pdf`` → fake :class:`UploadedBlob` (no Azure traffic).
  - ``extract_text`` → returns a fixed string per file (no Redis / pypdf).
  - ``extract_page1_signals`` → returns empty signals (no pypdf); the
    stubbed contact extractor ignores them anyway.
  - ``extract_contact_details`` → drives the contact result per filename
    so each test can shape what the bulk service sees without invoking
    Claude.
  - ``_enqueue_cv_pipeline`` → captures application_ids for assertion.

* HR auth uses the same ``/auth/login`` round-trip as the other
  integration suites — :func:`_login` returns the bearer token.

* The per-test transactional session in
  :mod:`tests.integration.conftest` rolls back every write on teardown,
  so the dev DB stays clean.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.contact_extractor import ContactDetails
from src.db.models.application import Application
from src.db.models.audit_log import AuditLog
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.job import Job
from src.db.models.user import User
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.audit import AuditAction, AuditEntityType
from src.enums.jobs import EmploymentType, JobStatus, WorkMode
from src.enums.roles import UserRole
from src.integrations.azure_blob import UploadedBlob
from src.schemas.bulk_upload import BulkUploadRejectionReason
from src.services import bulk_upload_service
from src.utils.password import hash_password
from src.utils.pdf import Page1Signals

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test data + monkeypatch helpers
# ---------------------------------------------------------------------------


PDF_HEADER_BYTES = b"%PDF-1.4\n"
PDF_BODY_PAD = b"0123456789" * 100


def _make_pdf(salt: bytes = b"") -> bytes:
    """Build a tiny valid-magic-byte PDF whose sha256 varies by ``salt``."""
    return PDF_HEADER_BYTES + PDF_BODY_PAD + salt + b"\n%%EOF\n"


@dataclass
class _BulkServiceStubs:
    """Container for the bulk-upload monkey-patch surface.

    ``dispatched`` accumulates application_ids the patched
    ``_enqueue_cv_pipeline`` was called with. ``pending_contacts`` is
    a FIFO of contact-extraction responses the patched
    :func:`extract_contact_details` will hand back in order — tests
    push the exact contacts they want before issuing the request.
    """

    dispatched: list[str] = field(default_factory=list)
    pending_contacts: list[ContactDetails] = field(default_factory=list)


def _unique_phone_e164() -> str:
    """Produce a unique synthetic +971 phone for tests that don't care."""
    return f"+9715{uuid.uuid4().int % 10_000_000:07d}"


def _default_contact() -> ContactDetails:
    return ContactDetails(
        full_name=f"Auto Candidate {uuid.uuid4().hex[:6]}",
        email=f"auto-{uuid.uuid4().hex[:8]}@example.com",
        phone=_unique_phone_e164(),
    )


def _patch_bulk_service(monkeypatch: pytest.MonkeyPatch) -> _BulkServiceStubs:
    """Stub blob upload, text extraction, contact extraction, and Celery dispatch.

    Returns a :class:`_BulkServiceStubs` the test fills with
    deterministic contact results (via ``stubs.pending_contacts``) and
    reads dispatched application_ids from.
    """
    stubs = _BulkServiceStubs()

    async def _fake_upload(pdf_bytes: bytes, *, candidate_id: uuid.UUID) -> UploadedBlob:
        digest = hashlib.sha256(pdf_bytes).hexdigest()
        return UploadedBlob(
            path=f"cvs/{candidate_id}/{digest}.pdf",
            url=f"https://test.blob/cvs/{candidate_id}/{digest}.pdf",
            sha256=digest,
        )

    monkeypatch.setattr(bulk_upload_service, "upload_pdf", _fake_upload)

    async def _fake_extract_text(_pdf_bytes: bytes) -> str:
        return "fake CV text"

    monkeypatch.setattr(bulk_upload_service, "extract_text", _fake_extract_text)

    async def _fake_page1_signals(_pdf_bytes: bytes) -> Page1Signals:
        return Page1Signals(name_candidates=[], deglued_text="")

    monkeypatch.setattr(bulk_upload_service, "extract_page1_signals", _fake_page1_signals)

    async def _fake_contact(_cv_text: str, **_kwargs: object) -> ContactDetails:
        # Accept name_candidates / contact_block_text by keyword without caring.
        if stubs.pending_contacts:
            return stubs.pending_contacts.pop(0)
        return _default_contact()

    monkeypatch.setattr(bulk_upload_service, "extract_contact_details", _fake_contact)

    def _fake_enqueue(application_id: uuid.UUID) -> None:
        stubs.dispatched.append(str(application_id))

    monkeypatch.setattr(bulk_upload_service, "_enqueue_cv_pipeline", _fake_enqueue)
    return stubs


async def _seed_hr_user(db_session: AsyncSession) -> tuple[User, str]:
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
) -> Job:
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
        status=status,
        public_slug=uuid.uuid4().hex[:16],
    )
    db_session.add(job)
    await db_session.flush()
    return job


def _files_payload(
    files: list[tuple[str, bytes]],
) -> list[tuple[str, tuple[str, bytes, str]]]:
    """Shape a list of (filename, bytes) into httpx multipart form data."""
    return [("files", (fn, body, "application/pdf")) for fn, body in files]


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


async def test_bulk_upload_requires_auth(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        f"/jobs/{uuid.uuid4()}/applications/bulk-upload",
        files=_files_payload([("cv.pdf", _make_pdf())]),
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_three_valid_pdfs_create_three_applications(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stubs = _patch_bulk_service(monkeypatch)
    user, password = await _seed_hr_user(db_session)
    job = await _seed_job(db_session, created_by=user.id)
    token = await _login(client, user.email, password)

    file_specs = [
        ("alice.pdf", _make_pdf(b"-alice")),
        ("bob.pdf", _make_pdf(b"-bob")),
        ("carol.pdf", _make_pdf(b"-carol")),
    ]
    # Three distinct contact identities so each file creates a new candidate.
    stubs.pending_contacts.extend(
        [
            ContactDetails(full_name="Alice A", email="alice@example.com", phone="+971501111111"),
            ContactDetails(full_name="Bob B", email="bob@example.com", phone="+971502222222"),
            ContactDetails(full_name="Carol C", email="carol@example.com", phone="+971503333333"),
        ]
    )

    resp = await client.post(
        f"/jobs/{job.id}/applications/bulk-upload",
        headers={"Authorization": f"Bearer {token}"},
        files=_files_payload(file_specs),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()

    assert body["accepted_count"] == 3
    assert body["rejected_count"] == 0
    assert len(body["applications"]) == 3
    assert body["rejected"] == []
    assert body["already_applied"] == []
    filenames = {item["filename"] for item in body["applications"]}
    assert filenames == {"alice.pdf", "bob.pdf", "carol.pdf"}

    # DB confirms three applications under this job.
    app_count = (
        await db_session.execute(
            sa.select(sa.func.count()).select_from(Application).where(Application.job_id == job.id)
        )
    ).scalar_one()
    assert app_count == 3

    # CV pipeline dispatched once per new application.
    assert len(stubs.dispatched) == 3
    assert set(stubs.dispatched) == {item["application_id"] for item in body["applications"]}

    # Per-application audit rows + one batch summary, all sharing batch_id.
    batch_id = body["batch_id"]
    audit_rows = (
        (
            await db_session.execute(
                sa.select(AuditLog).where(
                    AuditLog.action == AuditAction.BULK_UPLOAD.value,
                    AuditLog.user_id == user.id,
                )
            )
        )
        .scalars()
        .all()
    )
    per_app = [a for a in audit_rows if a.entity_type == AuditEntityType.APPLICATION.value]
    summary = [a for a in audit_rows if a.entity_type == AuditEntityType.JOB.value]
    assert len(per_app) == 3
    assert len(summary) == 1
    assert summary[0].after_state["batch_id"] == batch_id
    assert summary[0].after_state["accepted_count"] == 3
    assert summary[0].after_state["rejected_count"] == 0


# ---------------------------------------------------------------------------
# Regression: the service must not dispatch (the route does, post-commit)
# ---------------------------------------------------------------------------


async def test_process_batch_does_not_enqueue_pipeline(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``process_batch`` must NOT dispatch the CV pipeline itself.

    Enqueuing inside the still-uncommitted request transaction races the
    route's commit: the worker can pick up the task before the
    application row is visible and bail with
    ``cv_pipeline.dispatch.app_missing``, silently dropping the CV. The
    route owns the dispatch, *after* it commits. This pins the seam so a
    future edit can't quietly move the enqueue back inside the loop.
    """
    stubs = _patch_bulk_service(monkeypatch)
    user, _ = await _seed_hr_user(db_session)
    job = await _seed_job(db_session, created_by=user.id)

    stubs.pending_contacts.append(
        ContactDetails(full_name="Dana D", email="dana@example.com", phone="+971505555555")
    )

    response = await bulk_upload_service.process_batch(
        db_session,
        job_id=job.id,
        actor=user,
        ip=None,
        submissions=[
            bulk_upload_service.FileSubmission(
                filename="dana.pdf", pdf_bytes=_make_pdf(b"-dana"), prevalidation_error=None
            )
        ],
    )

    # One application created, but the service dispatched nothing — only
    # the route enqueues, and only after it commits.
    assert len(response.applications) == 1
    assert stubs.dispatched == []


# ---------------------------------------------------------------------------
# Mixed: 1 valid + 1 non-PDF + 1 oversized
# ---------------------------------------------------------------------------


async def test_mixed_batch_accepts_valid_rejects_others(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stubs = _patch_bulk_service(monkeypatch)
    user, password = await _seed_hr_user(db_session)
    job = await _seed_job(db_session, created_by=user.id)
    token = await _login(client, user.email, password)

    # 11 MB of zeros — the per-file cap defaults to cv_max_file_size_mb=10.
    oversized = b"X" * (11 * 1024 * 1024)
    non_pdf = b"This is not a PDF at all."

    file_specs = [
        ("good.pdf", _make_pdf(b"-good")),
        ("text.txt", non_pdf),
        ("huge.pdf", oversized),
    ]
    # Only the good file reaches contact extraction.
    stubs.pending_contacts.append(
        ContactDetails(full_name="Good Person", email="good@example.com", phone="+971504444444")
    )

    resp = await client.post(
        f"/jobs/{job.id}/applications/bulk-upload",
        headers={"Authorization": f"Bearer {token}"},
        files=_files_payload(file_specs),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()

    assert body["accepted_count"] == 1
    assert body["rejected_count"] == 2
    assert len(body["applications"]) == 1
    assert body["applications"][0]["filename"] == "good.pdf"

    rejections = {item["filename"]: item["reason"] for item in body["rejected"]}
    assert rejections["text.txt"] == BulkUploadRejectionReason.NOT_PDF.value
    assert rejections["huge.pdf"] == BulkUploadRejectionReason.TOO_LARGE.value

    # Only the good file enqueued.
    assert len(stubs.dispatched) == 1


# ---------------------------------------------------------------------------
# Duplicate sha256 inside one batch
# ---------------------------------------------------------------------------


async def test_duplicate_in_batch_rejects_second_occurrence(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stubs = _patch_bulk_service(monkeypatch)
    user, password = await _seed_hr_user(db_session)
    job = await _seed_job(db_session, created_by=user.id)
    token = await _login(client, user.email, password)

    body_bytes = _make_pdf(b"-same")
    file_specs = [("first.pdf", body_bytes), ("second.pdf", body_bytes)]
    stubs.pending_contacts.append(
        ContactDetails(full_name="Dup Person", email="dup@example.com", phone="+971505555555")
    )

    resp = await client.post(
        f"/jobs/{job.id}/applications/bulk-upload",
        headers={"Authorization": f"Bearer {token}"},
        files=_files_payload(file_specs),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()

    assert body["accepted_count"] == 1
    assert body["rejected_count"] == 1
    assert body["applications"][0]["filename"] == "first.pdf"
    assert body["rejected"][0]["filename"] == "second.pdf"
    assert body["rejected"][0]["reason"] == BulkUploadRejectionReason.DUPLICATE_IN_BATCH.value
    assert len(stubs.dispatched) == 1


# ---------------------------------------------------------------------------
# Already-applied candidate
# ---------------------------------------------------------------------------


async def test_candidate_already_applied_lands_in_already_applied_list(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stubs = _patch_bulk_service(monkeypatch)
    user, password = await _seed_hr_user(db_session)
    job = await _seed_job(db_session, created_by=user.id)
    token = await _login(client, user.email, password)

    # Seed a candidate + existing application on this job.
    existing_email = "prior@example.com"
    existing_phone = "+971501112222"
    existing_candidate = Candidate(
        email=existing_email, phone_e164=existing_phone, full_name="Prior Applicant"
    )
    db_session.add(existing_candidate)
    await db_session.flush()
    existing_cv = CvDocument(
        candidate_id=existing_candidate.id,
        blob_url="https://blob.invalid/prior.pdf",
        blob_sha256="a" * 64,
        is_current=True,
    )
    db_session.add(existing_cv)
    await db_session.flush()
    existing_application = Application(
        candidate_id=existing_candidate.id,
        job_id=job.id,
        cv_document_id=existing_cv.id,
        stage=ApplicationStage.VECTOR_SCREEN,
        status=ApplicationStatus.ACTIVE,
        consent_context={"ip": "1.2.3.4", "user_agent": "seed"},
        consented_at=datetime.now(UTC),
    )
    db_session.add(existing_application)
    await db_session.flush()

    file_specs = [("prior.pdf", _make_pdf(b"-prior"))]
    # Drive the contact stub to return the *same* identity as the
    # seeded candidate so the find-or-create lands on the existing row.
    stubs.pending_contacts.append(
        ContactDetails(
            full_name="Prior Applicant",
            email=existing_email,
            phone=existing_phone,
        )
    )

    resp = await client.post(
        f"/jobs/{job.id}/applications/bulk-upload",
        headers={"Authorization": f"Bearer {token}"},
        files=_files_payload(file_specs),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()

    assert body["accepted_count"] == 1
    assert body["applications"] == []
    assert len(body["already_applied"]) == 1
    assert body["already_applied"][0]["application_id"] == str(existing_application.id)
    assert stubs.dispatched == []


# ---------------------------------------------------------------------------
# Too many files → 400
# ---------------------------------------------------------------------------


async def test_more_than_max_files_returns_400(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_bulk_service(monkeypatch)
    user, password = await _seed_hr_user(db_session)
    job = await _seed_job(db_session, created_by=user.id)
    token = await _login(client, user.email, password)

    file_specs = [(f"cv-{i}.pdf", _make_pdf(bytes([i]))) for i in range(51)]
    resp = await client.post(
        f"/jobs/{job.id}/applications/bulk-upload",
        headers={"Authorization": f"Bearer {token}"},
        files=_files_payload(file_specs),
    )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# Closed job → 422
# ---------------------------------------------------------------------------


async def test_closed_job_returns_422(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_bulk_service(monkeypatch)
    user, password = await _seed_hr_user(db_session)
    job = await _seed_job(db_session, created_by=user.id, status=JobStatus.CLOSED)
    token = await _login(client, user.email, password)

    resp = await client.post(
        f"/jobs/{job.id}/applications/bulk-upload",
        headers={"Authorization": f"Bearer {token}"},
        files=_files_payload([("cv.pdf", _make_pdf())]),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "job_not_open_for_applications"


# ---------------------------------------------------------------------------
# parse_failed_no_contact
# ---------------------------------------------------------------------------


async def test_missing_contact_fields_rejected_with_parse_failed_no_contact(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stubs = _patch_bulk_service(monkeypatch)
    user, password = await _seed_hr_user(db_session)
    job = await _seed_job(db_session, created_by=user.id)
    token = await _login(client, user.email, password)

    stubs.pending_contacts.append(ContactDetails(full_name=None, email=None, phone=None))

    file_specs = [("nameless.pdf", _make_pdf(b"-nameless"))]
    resp = await client.post(
        f"/jobs/{job.id}/applications/bulk-upload",
        headers={"Authorization": f"Bearer {token}"},
        files=_files_payload(file_specs),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()

    assert body["accepted_count"] == 0
    assert body["rejected_count"] == 1
    assert body["rejected"][0]["filename"] == "nameless.pdf"
    assert body["rejected"][0]["reason"] == BulkUploadRejectionReason.PARSE_FAILED_NO_CONTACT.value
    assert stubs.dispatched == []
    # No candidate / application rows created.
    app_count = (
        await db_session.execute(
            sa.select(sa.func.count()).select_from(Application).where(Application.job_id == job.id)
        )
    ).scalar_one()
    assert app_count == 0


# ---------------------------------------------------------------------------
# partial contact (name + only one of email/phone) is accepted
# ---------------------------------------------------------------------------


async def test_phone_only_contact_is_accepted(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CV that yields name + phone but no email still creates a candidate."""
    stubs = _patch_bulk_service(monkeypatch)
    user, password = await _seed_hr_user(db_session)
    job = await _seed_job(db_session, created_by=user.id)
    token = await _login(client, user.email, password)

    stubs.pending_contacts.append(
        ContactDetails(full_name="Phone Only", email=None, phone="+971501234567")
    )

    resp = await client.post(
        f"/jobs/{job.id}/applications/bulk-upload",
        headers={"Authorization": f"Bearer {token}"},
        files=_files_payload([("phone_only.pdf", _make_pdf(b"-phone-only"))]),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()

    assert body["accepted_count"] == 1
    assert body["rejected_count"] == 0
    assert len(stubs.dispatched) == 1

    candidate_id = uuid.UUID(body["applications"][0]["candidate_id"])
    candidate = await db_session.get(Candidate, candidate_id)
    assert candidate is not None
    assert candidate.email is None
    assert candidate.phone_e164 == "+971501234567"
    assert candidate.full_name == "Phone Only"


async def test_email_only_contact_is_accepted(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CV that yields name + email but no phone still creates a candidate."""
    stubs = _patch_bulk_service(monkeypatch)
    user, password = await _seed_hr_user(db_session)
    job = await _seed_job(db_session, created_by=user.id)
    token = await _login(client, user.email, password)

    stubs.pending_contacts.append(
        ContactDetails(full_name="Email Only", email="emailonly@example.com", phone=None)
    )

    resp = await client.post(
        f"/jobs/{job.id}/applications/bulk-upload",
        headers={"Authorization": f"Bearer {token}"},
        files=_files_payload([("email_only.pdf", _make_pdf(b"-email-only"))]),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()

    assert body["accepted_count"] == 1
    assert body["rejected_count"] == 0
    assert len(stubs.dispatched) == 1

    candidate_id = uuid.UUID(body["applications"][0]["candidate_id"])
    candidate = await db_session.get(Candidate, candidate_id)
    assert candidate is not None
    assert candidate.email == "emailonly@example.com"
    assert candidate.phone_e164 is None
    assert candidate.full_name == "Email Only"


async def test_phone_only_contact_dedups_on_phone(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-uploading a different CV for the same phone-only candidate reuses
    the candidate row (partial-unique dedup on phone alone) and the second
    application on the same job lands in ``already_applied``."""
    stubs = _patch_bulk_service(monkeypatch)
    user, password = await _seed_hr_user(db_session)
    job = await _seed_job(db_session, created_by=user.id)
    token = await _login(client, user.email, password)

    # Two distinct PDFs, same phone-only identity.
    stubs.pending_contacts.extend(
        [
            ContactDetails(full_name="Repeat Phone", email=None, phone="+971509876543"),
            ContactDetails(full_name="Repeat Phone", email=None, phone="+971509876543"),
        ]
    )

    first = await client.post(
        f"/jobs/{job.id}/applications/bulk-upload",
        headers={"Authorization": f"Bearer {token}"},
        files=_files_payload([("first.pdf", _make_pdf(b"-first"))]),
    )
    assert first.status_code == 202, first.text
    first_body = first.json()
    assert len(first_body["applications"]) == 1
    first_candidate_id = first_body["applications"][0]["candidate_id"]

    second = await client.post(
        f"/jobs/{job.id}/applications/bulk-upload",
        headers={"Authorization": f"Bearer {token}"},
        files=_files_payload([("second.pdf", _make_pdf(b"-second"))]),
    )
    assert second.status_code == 202, second.text
    second_body = second.json()
    assert second_body["applications"] == []
    assert len(second_body["already_applied"]) == 1

    # Both uploads resolved to the *same* candidate — partial-unique dedup
    # matched on the phone alone (email NULL on both sides).
    assert second_body["already_applied"][0]["candidate_id"] == first_candidate_id
