"""Integration tests for the CV processing chain (Step 3.9).

Strategy
--------

Same shape as ``test_job_pipeline.py``: each async helper is driven
directly with the per-test transactional ``db_session`` and mocked AI /
blob / embedding calls. The Celery sync wrappers are not exercised
end-to-end (eager mode would open its own session and leak rows past
the rollback boundary) -- they're covered by a retry-config attribute
introspection test.

Doc Step 3.9 acceptance criteria covered:

* Full chain runs end-to-end on a sample CV:
    ``test_full_chain_end_to_end_marks_all_steps_ok``
* Application has ``similarity_score``, candidate has
  ``authenticity_score`` after pipeline:
    ``test_full_chain_end_to_end_marks_all_steps_ok``
* Below-threshold app auto-marked ``status='rejected'`` with stage
  staying at 'vector_screen':
    ``test_auto_reject_flips_status_when_below_threshold``
* All tasks idempotent:
    individual ``..._idempotent_*`` tests per step
* Failed task surfaces via ``pipeline_status``:
    ``test_extract_failure_marks_pipeline_status_failed`` etc.
* Retries: 3x for most, 5x for embedding:
    ``test_retry_kwargs_match_doc``
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.application import Application
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.job import Job
from src.db.models.user import User
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.authenticity import AuthenticityBand
from src.enums.authenticity_signals import BASELINE_SIGNAL_KEYS, SignalComputedBy, SignalKey
from src.enums.cv_pipeline import CvPipelineStep, CvPipelineStepState
from src.enums.events import AppEventState, AppEventType
from src.enums.jobs import (
    EmploymentType,
    JobStatus,
    NoticePeriod,
    VisaRequirement,
    WorkMode,
)
from src.enums.language import LanguageCode
from src.enums.roles import UserRole
from src.integrations.azure_blob import BlobNotFoundError
from src.schemas.authenticity import SignalResult
from src.schemas.authenticity_judge import JudgeResult
from src.schemas.events import AppEvent
from src.schemas.parsed_cv import ParsedCV, WorkEntry
from src.utils.password import hash_password
from src.utils.slug import generate_public_slug
from src.workers.tasks import cv_pipeline
from src.workers.tasks.cv_pipeline import (
    CV_EMBEDDING_MAX_RETRIES,
    CV_PIPELINE_MAX_RETRIES,
    TransientCvPipelineError,
    _merge_application_step_state,
    auto_reject_below_threshold,
    auto_reject_below_threshold_task,
    compute_application_authenticity,
    compute_application_authenticity_task,
    compute_pool_authenticity,
    compute_pool_authenticity_task,
    embed_cv,
    embed_cv_task,
    extract_cv_text,
    extract_text_task,
    parse_cv_for_document,
    parse_cv_task,
    score_similarity,
    score_similarity_task,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test fixtures + helpers
# ---------------------------------------------------------------------------


PDF_BYTES = b"%PDF-1.4\nfake pdf body for tests\n%%EOF\n"


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


async def _seed_job(db_session: AsyncSession, *, with_jd_embedding: bool = True) -> Job:
    user = await _seed_admin(db_session)
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
        languages_required=["english"],
        ai_verify_flags={},
        job_description="Build the Kabil hiring backend. Strong async Python.",
        status=JobStatus.OPEN,
        public_slug=generate_public_slug(),
        whatsapp_questions=[],
        pipeline_status={},
        ready_for_applications=True,
    )
    if with_jd_embedding:
        # Pre-populated unit-vector-ish JD embedding (1.0 in first dim, 0
        # elsewhere) so similarity tests have a stable input.
        vec = [0.0] * 1536
        vec[0] = 1.0
        job.jd_embedding = vec
    db_session.add(job)
    await db_session.flush()
    return job


async def _seed_candidate(db_session: AsyncSession) -> Candidate:
    candidate = Candidate(
        email=f"cand-{uuid.uuid4().hex[:8]}@example.com",
        phone_e164="+971501234567",
        full_name="Sample Candidate",
        parsed_profile={},
    )
    db_session.add(candidate)
    await db_session.flush()
    return candidate


async def _seed_cv_document(
    db_session: AsyncSession,
    candidate: Candidate,
    *,
    extracted_text: str | None = None,
    cv_embedding: list[float] | None = None,
    language: LanguageCode | None = None,
) -> CvDocument:
    digest = uuid.uuid4().hex + uuid.uuid4().hex  # 64-char hex placeholder
    cv_doc = CvDocument(
        candidate_id=candidate.id,
        blob_url=f"https://test.blob/cvs/{candidate.id}/{digest}.pdf",
        blob_sha256=digest,
        extracted_text=extracted_text,
        cv_embedding=cv_embedding,
        language=language,
        is_current=True,
    )
    db_session.add(cv_doc)
    await db_session.flush()
    return cv_doc


async def _seed_application(
    db_session: AsyncSession,
    *,
    candidate: Candidate,
    job: Job,
    cv_doc: CvDocument,
) -> Application:
    from datetime import UTC, datetime

    app = Application(
        candidate_id=candidate.id,
        job_id=job.id,
        cv_document_id=cv_doc.id,
        stage=ApplicationStage.VECTOR_SCREEN,
        status=ApplicationStatus.ACTIVE,
        consent_context={
            "ip": "127.0.0.1",
            "user_agent": "pytest",
            "accepted_at": datetime.now(UTC).isoformat(),
        },
        consented_at=datetime.now(UTC),
        pipeline_status={},
    )
    db_session.add(app)
    await db_session.flush()
    return app


def _make_unit_vector(boost_dim: int = 0, magnitude: float = 1.0) -> list[float]:
    """1536-d vector with mass concentrated on one dim. Used for similarity tests."""
    vec = [0.0] * 1536
    vec[boost_dim] = magnitude
    return vec


def _make_parsed_cv() -> ParsedCV:
    """Construct a valid ParsedCV for authenticity tests."""
    return ParsedCV(
        full_name="Sample Candidate",
        email="cand@example.com",
        phone="+971501234567",
        skills=["Python", "PostgreSQL"],
        work_history=[
            WorkEntry(
                company="Kabil",
                title="Senior Engineer",
                start="Jan 2020",
                end="Present",
                description="Built async Python backend with PostgreSQL.",
            ),
        ],
        education=[],
        total_experience_years=5.0,
        languages=["English"],
    )


# ---------------------------------------------------------------------------
# extract_cv_text helper
# ---------------------------------------------------------------------------


async def test_extract_cv_text_persists_text_and_language(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(db_session, candidate)
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    async def _fake_download(_path: str) -> bytes:
        return PDF_BYTES

    async def _fake_extract(_pdf: bytes) -> str:
        return "Senior Python engineer with five years experience at Kabil."

    monkeypatch.setattr(cv_pipeline, "download_pdf", _fake_download)
    monkeypatch.setattr(cv_pipeline, "extract_text", _fake_extract)

    await extract_cv_text(cv_doc.id, session=db_session)
    await db_session.refresh(cv_doc)
    await db_session.refresh(application)

    assert cv_doc.extracted_text is not None
    assert "Kabil" in cv_doc.extracted_text
    assert cv_doc.language is LanguageCode.EN
    assert (
        application.pipeline_status[CvPipelineStep.EXTRACT_TEXT.value]
        == CvPipelineStepState.OK.value
    )


async def test_extract_cv_text_idempotent_when_already_populated(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(
        db_session, candidate, extracted_text="already extracted", language=LanguageCode.EN
    )
    job = await _seed_job(db_session)
    await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    download_calls: list[str] = []

    async def _fake_download(path: str) -> bytes:
        download_calls.append(path)
        return PDF_BYTES

    monkeypatch.setattr(cv_pipeline, "download_pdf", _fake_download)

    await extract_cv_text(cv_doc.id, session=db_session)
    assert download_calls == []
    await db_session.refresh(cv_doc)
    assert cv_doc.extracted_text == "already extracted"


async def test_extract_cv_text_blob_missing_marks_failed(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(db_session, candidate)
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    async def _fake_download(_path: str) -> bytes:
        raise BlobNotFoundError("missing")

    monkeypatch.setattr(cv_pipeline, "download_pdf", _fake_download)

    await extract_cv_text(cv_doc.id, session=db_session)
    await db_session.refresh(application)
    assert (
        application.pipeline_status[CvPipelineStep.EXTRACT_TEXT.value]
        == CvPipelineStepState.FAILED.value
    )
    assert "extract_text_error" in application.pipeline_status


# ---------------------------------------------------------------------------
# parse_cv_for_document helper
# ---------------------------------------------------------------------------


async def test_parse_cv_writes_parsed_profile_to_candidate(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(
        db_session,
        candidate,
        extracted_text="Sample CV text mentioning Python and PostgreSQL at Kabil.",
        language=LanguageCode.EN,
    )
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    fake_parsed = _make_parsed_cv()

    async def _fake_parse(_text: str, _lang: LanguageCode) -> ParsedCV:
        return fake_parsed

    monkeypatch.setattr(cv_pipeline, "parse_cv", _fake_parse)

    await parse_cv_for_document(cv_doc.id, session=db_session)
    await db_session.refresh(candidate)
    await db_session.refresh(application)

    assert candidate.parsed_profile
    assert candidate.parsed_profile["full_name"] == "Sample Candidate"
    assert "Python" in candidate.parsed_profile["skills"]
    assert (
        application.pipeline_status[CvPipelineStep.PARSE_CV.value] == CvPipelineStepState.OK.value
    )


async def test_parse_cv_reconciles_name_but_not_identity(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A heuristic name (e.g. a section heading) is overwritten by parse_cv's
    authoritative name; email/phone identity is left untouched (stub)."""
    candidate = await _seed_candidate(db_session)
    # Simulate the synchronous contact-extractor having latched onto a section
    # heading instead of the real name (the "Core Competencies" bug).
    candidate.full_name = "Core Competencies"
    seeded_email = candidate.email
    seeded_phone = candidate.phone_e164
    cv_doc = await _seed_cv_document(
        db_session,
        candidate,
        extracted_text="Real Candidate — Senior Python engineer at Kabil.",
        language=LanguageCode.EN,
    )
    job = await _seed_job(db_session)
    await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    # parse_cv returns the real name plus a *different* email/phone.
    parsed = _make_parsed_cv()
    parsed = parsed.model_copy(
        update={
            "full_name": "Real Candidate",
            "email": "different@example.com",
            "phone": "+971509999999",
        }
    )

    async def _fake_parse(_text: str, _lang: LanguageCode) -> ParsedCV:
        return parsed

    monkeypatch.setattr(cv_pipeline, "parse_cv", _fake_parse)

    await parse_cv_for_document(cv_doc.id, session=db_session)
    await db_session.refresh(candidate)

    # Name corrected to parse_cv's authoritative value...
    assert candidate.full_name == "Real Candidate"
    # ...but identity (email/phone) left exactly as seeded — the stub only logs.
    assert candidate.email == seeded_email
    assert candidate.phone_e164 == seeded_phone


async def test_parse_cv_idempotent_when_profile_already_populated(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = await _seed_candidate(db_session)
    candidate.parsed_profile = {"full_name": "prepopulated"}
    cv_doc = await _seed_cv_document(
        db_session, candidate, extracted_text="text", language=LanguageCode.EN
    )
    job = await _seed_job(db_session)
    await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    calls: list[str] = []

    async def _fake_parse(text: str, _lang: LanguageCode) -> ParsedCV:
        calls.append(text)
        return _make_parsed_cv()

    monkeypatch.setattr(cv_pipeline, "parse_cv", _fake_parse)

    await parse_cv_for_document(cv_doc.id, session=db_session)
    assert calls == []


async def test_parse_cv_no_extracted_text_marks_failed(
    migrations_applied: None,
    db_session: AsyncSession,
) -> None:
    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(db_session, candidate, extracted_text=None)
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    await parse_cv_for_document(cv_doc.id, session=db_session)
    await db_session.refresh(application)

    assert (
        application.pipeline_status[CvPipelineStep.PARSE_CV.value]
        == CvPipelineStepState.FAILED.value
    )


# ---------------------------------------------------------------------------
# Realtime-event spy
# ---------------------------------------------------------------------------


def _spy_published(monkeypatch: pytest.MonkeyPatch) -> list[AppEvent]:
    """Capture envelopes the CV-pipeline helpers would publish, bypassing Redis."""
    captured: list[AppEvent] = []

    async def _capture(event: AppEvent) -> None:
        captured.append(event)

    monkeypatch.setattr(cv_pipeline, "publish_app_event", _capture)
    return captured


# ---------------------------------------------------------------------------
# compute_authenticity helper
# ---------------------------------------------------------------------------


def _fake_judge_result(score: float = 80.0, *, job_aware: bool = True) -> JudgeResult:
    keys = tuple(SignalKey) if job_aware else BASELINE_SIGNAL_KEYS
    signal = SignalResult(
        score=score,
        computed_by=SignalComputedBy.CLAUDE,
        finding="test finding",
        concern=score < 60.0,
        reasons=["test finding"],
        details={"score": score},
    )
    return JudgeResult(signals={key: signal for key in keys}, rationale="test rationale")


def _patch_judge(monkeypatch: pytest.MonkeyPatch, score: float = 80.0) -> None:
    async def _fake_judge(
        _text: str,
        _parsed: ParsedCV,
        *,
        job_title: str | None = None,
        required_skills: list[str] | None = None,
    ) -> JudgeResult:
        return _fake_judge_result(score, job_aware=job_title is not None)

    monkeypatch.setattr(cv_pipeline, "judge_authenticity", _fake_judge)


async def test_application_authenticity_writes_app_score_and_seeds_baseline(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = await _seed_candidate(db_session)
    candidate.parsed_profile = _make_parsed_cv().model_dump(mode="json")
    cv_doc = await _seed_cv_document(
        db_session,
        candidate,
        extracted_text="Sample English CV text " * 20,
        language=LanguageCode.EN,
    )
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    _patch_judge(monkeypatch, 80.0)
    published = _spy_published(monkeypatch)

    await compute_application_authenticity(application.id, session=db_session)
    await db_session.refresh(candidate)
    await db_session.refresh(application)

    # One event, for this application.
    assert len(published) == 1
    assert published[0].event is AppEventType.AUTHENTICITY
    assert published[0].state is AppEventState.OK
    assert published[0].application_id == application.id
    assert published[0].job_id == job.id

    # Application: job-aware (six-signal) verdict.
    assert application.authenticity_score is not None
    assert 0.0 <= application.authenticity_score <= 100.0
    assert application.authenticity_band in {
        AuthenticityBand.AUTHENTIC,
        AuthenticityBand.REVIEW,
        AuthenticityBand.FABRICATED,
    }
    assert application.authenticity_breakdown is not None
    assert "jd_keyword_mirroring" in application.authenticity_breakdown["breakdown"]
    assert application.authenticity_computed_at is not None

    # Candidate: job-agnostic baseline seeded (no JD Keyword Mirroring).
    assert candidate.authenticity_score is not None
    assert candidate.authenticity_breakdown is not None
    assert "specificity" in candidate.authenticity_breakdown["breakdown"]
    assert "jd_keyword_mirroring" not in candidate.authenticity_breakdown["breakdown"]
    assert candidate.authenticity_computed_at is not None

    assert (
        application.pipeline_status[CvPipelineStep.AUTHENTICITY.value]
        == CvPipelineStepState.OK.value
    )


async def test_application_authenticity_idempotent_when_already_computed(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    candidate = await _seed_candidate(db_session)
    candidate.parsed_profile = _make_parsed_cv().model_dump(mode="json")
    cv_doc = await _seed_cv_document(
        db_session, candidate, extracted_text="text " * 20, language=LanguageCode.EN
    )
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)
    application.authenticity_score = 88.0
    application.authenticity_band = AuthenticityBand.AUTHENTIC
    application.authenticity_breakdown = {"breakdown": {"specificity": {"score": 90.0}}}
    application.authenticity_computed_at = datetime.now(UTC)

    calls: list[str] = []

    async def _fake_judge(
        text: str,
        _parsed: ParsedCV,
        *,
        job_title: str | None = None,
        required_skills: list[str] | None = None,
    ) -> JudgeResult:
        calls.append(text)
        return _fake_judge_result()

    monkeypatch.setattr(cv_pipeline, "judge_authenticity", _fake_judge)

    await compute_application_authenticity(application.id, session=db_session)
    assert calls == []
    await db_session.refresh(application)
    assert application.authenticity_score == 88.0


async def test_pool_authenticity_writes_candidate_baseline(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = await _seed_candidate(db_session)
    candidate.parsed_profile = _make_parsed_cv().model_dump(mode="json")
    await _seed_cv_document(
        db_session,
        candidate,
        extracted_text="Sample English CV text " * 20,
        language=LanguageCode.EN,
    )

    _patch_judge(monkeypatch, 80.0)
    published = _spy_published(monkeypatch)

    await compute_pool_authenticity(candidate.id, session=db_session)
    await db_session.refresh(candidate)

    # Baseline only: five job-agnostic signals, no application/job, no event.
    assert candidate.authenticity_score is not None
    assert candidate.authenticity_breakdown is not None
    assert "jd_keyword_mirroring" not in candidate.authenticity_breakdown["breakdown"]
    assert published == []


# ---------------------------------------------------------------------------
# embed_cv helper
# ---------------------------------------------------------------------------


async def test_embed_cv_writes_vector(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(
        db_session, candidate, extracted_text="cv text", language=LanguageCode.EN
    )
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    async def _fake_embed(_text: str) -> list[float]:
        return _make_unit_vector(boost_dim=0)

    monkeypatch.setattr(cv_pipeline, "embed_text", _fake_embed)

    await embed_cv(cv_doc.id, session=db_session)
    await db_session.refresh(cv_doc)
    await db_session.refresh(application)

    assert cv_doc.cv_embedding is not None
    assert len(cv_doc.cv_embedding) == 1536
    assert (
        application.pipeline_status[CvPipelineStep.EMBED_CV.value] == CvPipelineStepState.OK.value
    )


async def test_embed_cv_idempotent_when_already_populated(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(
        db_session,
        candidate,
        extracted_text="text",
        language=LanguageCode.EN,
        cv_embedding=_make_unit_vector(boost_dim=5),
    )
    job = await _seed_job(db_session)
    await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    calls: list[str] = []

    async def _fake_embed(text: str) -> list[float]:
        calls.append(text)
        return _make_unit_vector()

    monkeypatch.setattr(cv_pipeline, "embed_text", _fake_embed)
    await embed_cv(cv_doc.id, session=db_session)
    assert calls == []


# ---------------------------------------------------------------------------
# score_similarity helper
# ---------------------------------------------------------------------------


async def test_score_similarity_computes_cosine_similarity(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = await _seed_candidate(db_session)
    # CV embedding identical to JD embedding -> similarity == 100.0
    cv_doc = await _seed_cv_document(
        db_session,
        candidate,
        extracted_text="text",
        language=LanguageCode.EN,
        cv_embedding=_make_unit_vector(boost_dim=0),
    )
    job = await _seed_job(db_session)  # JD embedding boosts dim 0 too
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)
    published = _spy_published(monkeypatch)

    await score_similarity(application.id, session=db_session)
    await db_session.refresh(application)

    assert application.similarity_score is not None
    assert application.similarity_score == pytest.approx(100.0, abs=1e-7)
    assert (
        application.pipeline_status[CvPipelineStep.SIMILARITY.value] == CvPipelineStepState.OK.value
    )

    # A single OK similarity nudge fires for this application.
    assert len(published) == 1
    assert published[0].event is AppEventType.SIMILARITY
    assert published[0].state is AppEventState.OK
    assert published[0].application_id == application.id
    assert published[0].job_id == job.id


async def test_score_similarity_no_cv_embedding_marks_failed(
    migrations_applied: None,
    db_session: AsyncSession,
) -> None:
    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(db_session, candidate, extracted_text="text")
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    await score_similarity(application.id, session=db_session)
    await db_session.refresh(application)

    assert application.similarity_score is None
    assert (
        application.pipeline_status[CvPipelineStep.SIMILARITY.value]
        == CvPipelineStepState.FAILED.value
    )


# ---------------------------------------------------------------------------
# auto_reject_below_threshold helper
# ---------------------------------------------------------------------------


async def test_auto_reject_flips_status_when_below_threshold(
    migrations_applied: None,
    db_session: AsyncSession,
) -> None:
    """Doc acceptance: 'Below-threshold app auto-marked status=rejected with
    stage staying at vector_screen'.
    """
    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(db_session, candidate, extracted_text="text")
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)
    # Set similarity below floor: distance = 100 - 30 = 70 > 60 threshold.
    application.similarity_score = 30.0
    await db_session.flush()

    await auto_reject_below_threshold(application.id, session=db_session)
    await db_session.refresh(application)

    assert application.status is ApplicationStatus.REJECTED
    assert application.stage is ApplicationStage.VECTOR_SCREEN
    assert (
        application.pipeline_status[CvPipelineStep.AUTO_REJECT.value]
        == CvPipelineStepState.OK.value
    )


async def test_auto_reject_keeps_active_when_above_threshold(
    migrations_applied: None,
    db_session: AsyncSession,
) -> None:
    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(db_session, candidate, extracted_text="text")
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)
    # similarity 80 -> distance 20 < 60 -> keep ACTIVE.
    application.similarity_score = 80.0
    await db_session.flush()

    await auto_reject_below_threshold(application.id, session=db_session)
    await db_session.refresh(application)

    assert application.status is ApplicationStatus.ACTIVE
    assert (
        application.pipeline_status[CvPipelineStep.AUTO_REJECT.value]
        == CvPipelineStepState.SKIPPED.value
    )


async def test_auto_reject_idempotent_on_rerun(
    migrations_applied: None,
    db_session: AsyncSession,
) -> None:
    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(db_session, candidate, extracted_text="text")
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)
    application.similarity_score = 20.0
    await db_session.flush()

    await auto_reject_below_threshold(application.id, session=db_session)
    await auto_reject_below_threshold(application.id, session=db_session)
    await db_session.refresh(application)
    assert application.status is ApplicationStatus.REJECTED


# ---------------------------------------------------------------------------
# Full chain end-to-end (helper-driven; eager Celery mode would leak state)
# ---------------------------------------------------------------------------


async def test_full_chain_end_to_end_marks_all_steps_ok(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive all six helpers in order and assert the doc's acceptance contract.

    'Full chain runs end-to-end on a sample CV. Application has
    similarity_score, candidate has authenticity_score after pipeline.'
    """
    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(db_session, candidate)
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    async def _fake_download(_path: str) -> bytes:
        return PDF_BYTES

    async def _fake_extract(_pdf: bytes) -> str:
        return "Senior Python backend engineer with PostgreSQL experience. " * 10

    async def _fake_parse(_text: str, _lang: LanguageCode) -> ParsedCV:
        return _make_parsed_cv()

    async def _fake_embed(_text: str) -> list[float]:
        # CV embedding matches JD embedding -> similarity 100 -> NOT rejected.
        return _make_unit_vector(boost_dim=0)

    monkeypatch.setattr(cv_pipeline, "download_pdf", _fake_download)
    monkeypatch.setattr(cv_pipeline, "extract_text", _fake_extract)
    monkeypatch.setattr(cv_pipeline, "parse_cv", _fake_parse)
    _patch_judge(monkeypatch, 85.0)
    monkeypatch.setattr(cv_pipeline, "embed_text", _fake_embed)

    await extract_cv_text(cv_doc.id, session=db_session)
    await parse_cv_for_document(cv_doc.id, session=db_session)
    await compute_application_authenticity(application.id, session=db_session)
    await embed_cv(cv_doc.id, session=db_session)
    await score_similarity(application.id, session=db_session)
    await auto_reject_below_threshold(application.id, session=db_session)

    await db_session.refresh(application)
    await db_session.refresh(candidate)
    await db_session.refresh(cv_doc)

    # Application: similarity_score populated, status still ACTIVE (above threshold).
    assert application.similarity_score == pytest.approx(100.0, abs=1e-7)
    assert application.status is ApplicationStatus.ACTIVE
    assert application.stage is ApplicationStage.VECTOR_SCREEN

    # Application: job-aware authenticity; candidate: baseline seeded.
    assert application.authenticity_score is not None
    assert application.authenticity_band is not None
    assert candidate.authenticity_score is not None
    assert candidate.parsed_profile

    # CV document: text + embedding populated.
    assert cv_doc.extracted_text
    assert cv_doc.cv_embedding is not None

    # Every step OK except auto_reject which is SKIPPED (above threshold).
    pipeline = application.pipeline_status
    for step in (
        CvPipelineStep.EXTRACT_TEXT,
        CvPipelineStep.PARSE_CV,
        CvPipelineStep.AUTHENTICITY,
        CvPipelineStep.EMBED_CV,
        CvPipelineStep.SIMILARITY,
    ):
        assert pipeline[step.value] == CvPipelineStepState.OK.value
    assert pipeline[CvPipelineStep.AUTO_REJECT.value] == CvPipelineStepState.SKIPPED.value


async def test_full_chain_below_threshold_rejects(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc acceptance: 'Below-threshold app auto-marked status=rejected with
    stage staying at vector_screen'.
    """
    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(db_session, candidate)
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    async def _fake_download(_path: str) -> bytes:
        return PDF_BYTES

    async def _fake_extract(_pdf: bytes) -> str:
        return "irrelevant CV " * 50

    async def _fake_parse(_text: str, _lang: LanguageCode) -> ParsedCV:
        return _make_parsed_cv()

    async def _fake_embed(_text: str) -> list[float]:
        # CV embedding orthogonal to JD embedding -> similarity 0 -> reject.
        return _make_unit_vector(boost_dim=100)

    monkeypatch.setattr(cv_pipeline, "download_pdf", _fake_download)
    monkeypatch.setattr(cv_pipeline, "extract_text", _fake_extract)
    monkeypatch.setattr(cv_pipeline, "parse_cv", _fake_parse)
    _patch_judge(monkeypatch, 85.0)
    monkeypatch.setattr(cv_pipeline, "embed_text", _fake_embed)

    await extract_cv_text(cv_doc.id, session=db_session)
    await parse_cv_for_document(cv_doc.id, session=db_session)
    await compute_application_authenticity(application.id, session=db_session)
    await embed_cv(cv_doc.id, session=db_session)
    await score_similarity(application.id, session=db_session)
    await auto_reject_below_threshold(application.id, session=db_session)

    await db_session.refresh(application)

    assert application.similarity_score == pytest.approx(0.0, abs=1e-9)
    assert application.status is ApplicationStatus.REJECTED
    # Doc-mandated: stage stays at vector_screen.
    assert application.stage is ApplicationStage.VECTOR_SCREEN
    assert (
        application.pipeline_status[CvPipelineStep.AUTO_REJECT.value]
        == CvPipelineStepState.OK.value
    )


# ---------------------------------------------------------------------------
# Transient retry surfacing
# ---------------------------------------------------------------------------


async def test_parse_transient_anthropic_raises_for_celery_retry(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient Anthropic errors bubble up as TransientCvPipelineError so
    Celery's autoretry triggers.
    """
    import anthropic

    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(
        db_session, candidate, extracted_text="hello world", language=LanguageCode.EN
    )
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    async def _fake_parse(_text: str, _lang: LanguageCode) -> ParsedCV:
        raise anthropic.APIConnectionError(request=Any)  # type: ignore[arg-type]

    monkeypatch.setattr(cv_pipeline, "parse_cv", _fake_parse)

    with pytest.raises(TransientCvPipelineError):
        await parse_cv_for_document(cv_doc.id, session=db_session)

    await db_session.refresh(application)
    assert (
        application.pipeline_status[CvPipelineStep.PARSE_CV.value]
        == CvPipelineStepState.PENDING.value
    )


async def test_embed_transient_openai_raises_for_celery_retry(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    import openai

    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(
        db_session, candidate, extracted_text="hello world", language=LanguageCode.EN
    )
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    async def _fake_embed(_text: str) -> list[float]:
        raise openai.APIConnectionError(request=httpx.Request("POST", "https://test"))

    monkeypatch.setattr(cv_pipeline, "embed_text", _fake_embed)

    with pytest.raises(TransientCvPipelineError):
        await embed_cv(cv_doc.id, session=db_session)

    await db_session.refresh(application)
    assert (
        application.pipeline_status[CvPipelineStep.EMBED_CV.value]
        == CvPipelineStepState.PENDING.value
    )


# ---------------------------------------------------------------------------
# Celery wrapper retry config introspection
# ---------------------------------------------------------------------------


def test_retry_kwargs_match_doc() -> None:
    """Doc Step 3.9 acceptance: 'Retries: 3x with exp backoff (default), 5x
    for embedding tasks'.
    """
    # Embedding gets the larger budget; everything else gets the default 3.
    assert embed_cv_task.retry_kwargs["max_retries"] == CV_EMBEDDING_MAX_RETRIES == 5

    for task in (
        extract_text_task,
        parse_cv_task,
        compute_application_authenticity_task,
        compute_pool_authenticity_task,
        score_similarity_task,
        auto_reject_below_threshold_task,
    ):
        assert task.retry_kwargs["max_retries"] == CV_PIPELINE_MAX_RETRIES == 3


def test_all_tasks_autoretry_on_transient_pipeline_error() -> None:
    for task in (
        extract_text_task,
        parse_cv_task,
        compute_application_authenticity_task,
        compute_pool_authenticity_task,
        embed_cv_task,
        score_similarity_task,
        auto_reject_below_threshold_task,
    ):
        assert TransientCvPipelineError in task.autoretry_for


# ---------------------------------------------------------------------------
# pipeline_status atomic-merge (post-fix race guard)
# ---------------------------------------------------------------------------


async def test_merge_application_step_state_preserves_existing_keys(
    migrations_applied: None,
    db_session: AsyncSession,
) -> None:
    """Atomic-merge guard.

    Two CV-pipeline tasks (e.g. parse + embed) touching the same
    application would have clobbered each other's pipeline_status keys
    under the old Python-side read-modify-write. The atomic SQL merge
    (``pipeline_status - error_key || patch::jsonb``) preserves every
    prior key because Postgres holds a row lock for the duration of the
    UPDATE.
    """
    import sqlalchemy as sa

    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(db_session, candidate)
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    # Seed an "extract_text already finished" key bypassing the helper.
    await db_session.execute(
        sa.text("UPDATE applications SET pipeline_status = CAST(:ps AS JSONB) WHERE id = :id"),
        {"ps": '{"extract_text": "ok"}', "id": str(application.id)},
    )
    await db_session.flush()

    # Merge a different step; ``extract_text`` must survive.
    await _merge_application_step_state(
        db_session,
        where=Application.id == application.id,
        step=CvPipelineStep.PARSE_CV,
        state=CvPipelineStepState.OK,
    )
    await db_session.refresh(application)

    assert application.pipeline_status["extract_text"] == CvPipelineStepState.OK.value
    assert application.pipeline_status["parse_cv"] == CvPipelineStepState.OK.value


async def test_merge_application_step_state_bulk_update_handles_multiple_apps(
    migrations_applied: None,
    db_session: AsyncSession,
) -> None:
    """One UPDATE statement writes to every application matching the where clause.

    extract / parse / embed work per-cv_document, which may have many
    applications attached. authenticity works per-candidate, ditto. The
    helper must touch all of them atomically.
    """
    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(db_session, candidate)
    job1 = await _seed_job(db_session)
    job2 = await _seed_job(db_session)
    app1 = await _seed_application(db_session, candidate=candidate, job=job1, cv_doc=cv_doc)
    app2 = await _seed_application(db_session, candidate=candidate, job=job2, cv_doc=cv_doc)

    await _merge_application_step_state(
        db_session,
        where=Application.cv_document_id == cv_doc.id,
        step=CvPipelineStep.EXTRACT_TEXT,
        state=CvPipelineStepState.OK,
    )
    await db_session.refresh(app1)
    await db_session.refresh(app2)

    assert app1.pipeline_status["extract_text"] == CvPipelineStepState.OK.value
    assert app2.pipeline_status["extract_text"] == CvPipelineStepState.OK.value


async def test_merge_application_step_state_clears_prior_error_on_recovery(
    migrations_applied: None,
    db_session: AsyncSession,
) -> None:
    """A successful retry must remove the prior ``{step}_error`` key."""
    import sqlalchemy as sa

    candidate = await _seed_candidate(db_session)
    cv_doc = await _seed_cv_document(db_session, candidate)
    job = await _seed_job(db_session)
    application = await _seed_application(db_session, candidate=candidate, job=job, cv_doc=cv_doc)

    await db_session.execute(
        sa.text("UPDATE applications SET pipeline_status = CAST(:ps AS JSONB) WHERE id = :id"),
        {
            "ps": '{"embed_cv": "failed", "embed_cv_error": "transient gone"}',
            "id": str(application.id),
        },
    )
    await db_session.flush()

    await _merge_application_step_state(
        db_session,
        where=Application.id == application.id,
        step=CvPipelineStep.EMBED_CV,
        state=CvPipelineStepState.OK,
    )
    await db_session.refresh(application)

    assert application.pipeline_status["embed_cv"] == CvPipelineStepState.OK.value
    assert "embed_cv_error" not in application.pipeline_status
