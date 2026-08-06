"""CV processing Celery pipeline (Step 3.9).

The dispatcher :func:`dispatch_cv_processing_task` (called by the public
upload endpoint) composes the chain

    extract_text -> parse_cv -> authenticity -> embed_cv ->
    score_similarity -> auto_reject_below_threshold

Six tasks, six rows in ``applications.pipeline_status``. Each task gets
*only* the id the architecture doc prescribes (cv_document_id /
candidate_id / application_id), threaded through ``.si()`` immutable
signatures so no upstream return value leaks into the next call.

The split between *async helpers* and *sync task wrappers* mirrors
:mod:`src.workers.tasks.job_pipeline`: helpers do the real work and take
an :class:`AsyncSession`, so tests can drive them directly against the
per-test transactional session fixture; the wrappers exist purely to
bridge Celery's sync execution model to the async stack via
:func:`asyncio.run`.

Idempotency is guaranteed at every step:

* each helper checks whether its output is already populated and bails
  out early if so (no Claude / OpenAI call on a re-run);
* the underlying caches in :mod:`src.utils.pdf`, :mod:`src.ai.claude`,
  and :mod:`src.ai.embeddings` cover any work that does happen.

Retries follow the architecture doc: 3x with exponential backoff for
most tasks, 5x for the embedding task (transient OpenAI failures are
the most common reason any step fails). Non-transient failures (a
missing blob, a corrupt PDF that pypdf can't read, a Claude judge
falling back to neutral) are *not* re-raised: each helper writes
``FAILED`` to ``pipeline_status`` and returns, so the chain continues
without exploding -- downstream tasks then find missing prerequisites
and themselves write ``SKIPPED`` / ``FAILED``.

CV bytes, extracted text, parsed profiles, and embeddings are never
logged -- they're candidate-PII / confidential. Log records carry
identifiers and short status flags only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import uuid
from datetime import UTC, datetime
from typing import Any, Final

import anthropic
import openai
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.cv_parser import parse_cv
from src.ai.embeddings import embed_text
from src.config import settings
from src.db.models.application import Application
from src.db.models.application_score import ApplicationScore
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.job import Job
from src.db.session import SessionFactory
from src.enums.applications import ApplicationStatus
from src.enums.authenticity_signals import BASELINE_SIGNAL_KEYS
from src.enums.cv_pipeline import CvPipelineStep, CvPipelineStepState
from src.enums.events import AppEventState, AppEventType
from src.enums.score_types import ScoreModel, ScoreType
from src.enums.thresholds import SIMILARITY_REJECTION_THRESHOLD
from src.integrations.azure_blob import CV_BLOB_PATH_TEMPLATE, BlobNotFoundError, download_pdf
from src.schemas.authenticity import AuthenticityResult
from src.schemas.events import AppEvent
from src.schemas.parsed_cv import ParsedCV
from src.services.authenticity.aggregator import aggregate as aggregate_authenticity
from src.services.authenticity.llm_judge import judge_authenticity
from src.services.events_service import publish_app_event
from src.services.scoring.similarity_breakdown import compute_similarity_breakdown
from src.utils.language import detect_language
from src.utils.pdf import extract_text
from src.utils.phone import InvalidPhoneNumberError, normalize_phone
from src.workers.celery_app import SLOW_QUEUE, celery_app

logger = logging.getLogger(__name__)

# --- Retry constants --------------------------------------------------------

# Per doc Step 3.9: "Retries: 3x with exp backoff (default), 5x for
# embedding tasks (transient OpenAI failures)".
CV_PIPELINE_MAX_RETRIES: Final[int] = 3
CV_EMBEDDING_MAX_RETRIES: Final[int] = 5
CV_PIPELINE_RETRY_BACKOFF_SECONDS: Final[int] = 60

# Provider-side exceptions we treat as worth retrying. Anything else
# (validation, schema, missing row, corrupt PDF) is permanent and gets
# recorded as FAILED on pipeline_status without raising.
_TRANSIENT_OPENAI: Final[tuple[type[Exception], ...]] = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)
_TRANSIENT_ANTHROPIC: Final[tuple[type[Exception], ...]] = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)


class TransientCvPipelineError(RuntimeError):
    """Raised inside a helper to signal "Celery should retry me later".

    The wrapped provider exception lives on ``__cause__`` for log triage.
    """


# --- Atomic status-mutation helpers -----------------------------------------
#
# Each chain step writes its own row in ``applications.pipeline_status``
# (a JSONB blob keyed by ``CvPipelineStep`` value). When the chain
# fans out -- e.g. two parallel ``extract_text_task`` calls for two
# candidates sharing one job, or a re-run racing a fresh dispatch --
# the historical Python-side read-modify-write pattern would clobber
# whichever task committed second. We avoid that by merging at the SQL
# layer: ``UPDATE applications SET pipeline_status = (pipeline_status -
# error_key) || patch::jsonb WHERE <where>``. The row lock Postgres
# holds during the UPDATE serializes concurrent merges.


async def _merge_application_step_state(
    session: AsyncSession,
    *,
    where: Any,
    step: CvPipelineStep,
    state: CvPipelineStepState,
    error: str | None = None,
) -> None:
    """Atomically merge ``{step: state}`` into ``applications.pipeline_status``.

    The ``where`` clause is a single SQLAlchemy expression -- typically
    ``Application.cv_document_id == <id>`` (extract / parse / embed),
    ``Application.candidate_id == <id>`` (authenticity), or
    ``Application.id == <id>`` (similarity / auto_reject). A single
    UPDATE walks all matching rows; Postgres takes per-row locks so
    concurrent merges from sibling tasks do not race.
    """
    error_key = f"{step.value}_error"
    patch: dict[str, str] = {step.value: state.value}
    if state is CvPipelineStepState.FAILED and error is not None:
        patch[error_key] = error

    # See job_pipeline._merge_step_state for why this binding shape.
    patch_jsonb = sa.cast(sa.literal(json.dumps(patch)), JSONB)

    stmt = (
        sa.update(Application)
        .where(where)
        .values(
            pipeline_status=Application.pipeline_status.op("-")(error_key).op("||")(patch_jsonb)
        )
    )
    await session.execute(stmt)


# --- Database loaders -------------------------------------------------------


async def _load_cv_document(session: AsyncSession, cv_document_id: uuid.UUID) -> CvDocument | None:
    return await session.get(CvDocument, cv_document_id)


async def _load_candidate(session: AsyncSession, candidate_id: uuid.UUID) -> Candidate | None:
    return await session.get(Candidate, candidate_id)


async def _load_application(session: AsyncSession, application_id: uuid.UUID) -> Application | None:
    return await session.get(Application, application_id)


async def _current_cv_for_candidate(
    session: AsyncSession, candidate_id: uuid.UUID
) -> CvDocument | None:
    stmt = select(CvDocument).where(
        CvDocument.candidate_id == candidate_id,
        CvDocument.is_current.is_(True),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# --- Vector math ------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors, on a 0-100 scale.

    Returned as a percentage (cosine x 100) so ``similarity_score`` reads
    as a percent match everywhere it surfaces. OpenAI's
    text-embedding-3-small returns unit vectors so the dot product alone
    would suffice; we still normalise defensively so a future switch to a
    non-unit-vector model wouldn't silently corrupt similarity scores.
    """
    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    # pgvector deserializes embedding columns to numpy arrays, so the
    # operands are numpy float32 scalars and the quotient is np.float32 —
    # not JSON-serializable when it lands in ApplicationScore.value /
    # .breakdown. Cast to a native float so the annotation holds and the
    # downstream JSONB write succeeds.
    return float(dot / (norm_a * norm_b)) * 100.0


# --- Async helpers (real work) ----------------------------------------------


async def extract_cv_text(cv_document_id: uuid.UUID, *, session: AsyncSession) -> None:
    """Download the blob, extract text + detect language, persist on the CV.

    Idempotent: if ``extracted_text`` is already populated, returns
    immediately after marking ``OK`` on every linked application.
    """
    cv_doc = await _load_cv_document(session, cv_document_id)
    if cv_doc is None:
        logger.warning(
            "cv_pipeline.extract.cv_missing", extra={"cv_document_id": str(cv_document_id)}
        )
        return

    where_apps = Application.cv_document_id == cv_document_id

    if cv_doc.extracted_text is not None:
        await _merge_application_step_state(
            session,
            where=where_apps,
            step=CvPipelineStep.EXTRACT_TEXT,
            state=CvPipelineStepState.OK,
        )
        await session.commit()
        logger.info("cv_pipeline.extract.skipped", extra={"cv_document_id": str(cv_document_id)})
        return

    blob_path = CV_BLOB_PATH_TEMPLATE.format(
        candidate_id=cv_doc.candidate_id, sha256=cv_doc.blob_sha256
    )
    try:
        pdf_bytes = await download_pdf(blob_path)
    except BlobNotFoundError as exc:
        await _merge_application_step_state(
            session,
            where=where_apps,
            step=CvPipelineStep.EXTRACT_TEXT,
            state=CvPipelineStepState.FAILED,
            error=str(exc),
        )
        await session.commit()
        logger.warning(
            "cv_pipeline.extract.blob_missing", extra={"cv_document_id": str(cv_document_id)}
        )
        return

    try:
        text = await extract_text(pdf_bytes)
    except Exception as exc:
        # extract_text itself never raises for corrupt PDFs (returns "" and
        # logs a warning); a true exception here is a timeout or wiring bug.
        await _merge_application_step_state(
            session,
            where=where_apps,
            step=CvPipelineStep.EXTRACT_TEXT,
            state=CvPipelineStepState.FAILED,
            error=str(exc),
        )
        await session.commit()
        logger.exception(
            "cv_pipeline.extract.failed", extra={"cv_document_id": str(cv_document_id)}
        )
        return

    cv_doc.extracted_text = text
    cv_doc.language = detect_language(text) if text else None
    await _merge_application_step_state(
        session, where=where_apps, step=CvPipelineStep.EXTRACT_TEXT, state=CvPipelineStepState.OK
    )
    await session.commit()
    logger.info(
        "cv_pipeline.extract.ok",
        extra={
            "cv_document_id": str(cv_document_id),
            "chars_extracted": len(text),
            "language": cv_doc.language.value if cv_doc.language else None,
        },
    )


def _reconcile_candidate_identity(candidate: Candidate, parsed: ParsedCV) -> None:
    """Correct the candidate's name from the authoritative ``parse_cv`` output.

    The candidate row is created synchronously at upload from
    :func:`src.ai.contact_extractor.extract_contact_details`, whose deterministic
    fast-path picks the name from the largest-font page-1 text. That heuristic
    can latch onto a section heading ("Core Competencies") when it out-ranks the
    real name, and because it returns a plausible-looking value the Claude
    fallback never fires. ``parse_cv`` (Claude Sonnet) reads the whole CV with a
    proper "the candidate's own name as written" rubric, so we treat *its*
    ``full_name`` as authoritative and overwrite a divergent heuristic name.

    Name is a plain display column (``Candidate.full_name``) — overwriting it
    carries no constraint risk.

    Email / phone are deliberately **not** reconciled here. They form the
    candidate's composite identity (partial-UNIQUE on ``LOWER(email)`` /
    ``phone_e164``), are shared across every application for this person, and on
    the public-apply path come from what the applicant typed into the form.
    Mutating them is an identity change requiring find-or-merge logic (does the
    new pair already belong to another candidate?), not a field edit — so for
    now we only **detect and log** divergence and leave the row untouched.

    Mutates ``candidate`` in place; the caller commits.
    """
    parsed_name = (parsed.full_name or "").strip()
    if parsed_name and parsed_name != (candidate.full_name or "").strip():
        # Names are quasi-identifiers; keep them out of logs (PII). Record only
        # that a correction happened so we can see the heuristic being overridden.
        logger.info(
            "cv_pipeline.parse.name_reconciled",
            extra={"candidate_id": str(candidate.id)},
        )
        candidate.full_name = parsed_name

    # --- email / phone: detect-and-log stub (no identity mutation) -----------
    parsed_email = (parsed.email or "").strip().lower()
    if parsed_email and parsed_email != (candidate.email or "").strip().lower():
        # TODO(identity-reconcile): filling/overwriting requires checking the
        # partial-UNIQUE indexes and merging into any existing candidate that
        # already owns the (email, phone) pair. Log-only until that lands.
        logger.info(
            "cv_pipeline.parse.email_mismatch",
            extra={
                "candidate_id": str(candidate.id),
                "candidate_email_present": candidate.email is not None,
            },
        )

    parsed_phone = (parsed.phone or "").strip()
    if parsed_phone:
        try:
            parsed_phone_e164 = normalize_phone(
                parsed_phone, default_region=settings.default_phone_region
            )
        except InvalidPhoneNumberError:
            parsed_phone_e164 = None
        if parsed_phone_e164 and parsed_phone_e164 != candidate.phone_e164:
            # Same constraint caveat as email; see TODO above.
            logger.info(
                "cv_pipeline.parse.phone_mismatch",
                extra={
                    "candidate_id": str(candidate.id),
                    "candidate_phone_present": candidate.phone_e164 is not None,
                },
            )


async def parse_cv_for_document(cv_document_id: uuid.UUID, *, session: AsyncSession) -> None:
    """Parse the CV's extracted text into the candidate's ``parsed_profile``.

    Idempotent: if the candidate already has a populated ``parsed_profile``,
    returns immediately after marking ``OK``.
    """
    cv_doc = await _load_cv_document(session, cv_document_id)
    if cv_doc is None:
        logger.warning(
            "cv_pipeline.parse.cv_missing", extra={"cv_document_id": str(cv_document_id)}
        )
        return

    where_apps = Application.cv_document_id == cv_document_id

    candidate = await _load_candidate(session, cv_doc.candidate_id)
    if candidate is None:
        await _merge_application_step_state(
            session,
            where=where_apps,
            step=CvPipelineStep.PARSE_CV,
            state=CvPipelineStepState.FAILED,
            error="candidate_missing",
        )
        await session.commit()
        return

    if candidate.parsed_profile:
        await _merge_application_step_state(
            session, where=where_apps, step=CvPipelineStep.PARSE_CV, state=CvPipelineStepState.OK
        )
        await session.commit()
        logger.info(
            "cv_pipeline.parse.skipped",
            extra={"cv_document_id": str(cv_document_id), "candidate_id": str(candidate.id)},
        )
        return

    if not cv_doc.extracted_text:
        await _merge_application_step_state(
            session,
            where=where_apps,
            step=CvPipelineStep.PARSE_CV,
            state=CvPipelineStepState.FAILED,
            error="extracted_text_missing",
        )
        await session.commit()
        logger.warning("cv_pipeline.parse.no_text", extra={"cv_document_id": str(cv_document_id)})
        return

    language = cv_doc.language or detect_language(cv_doc.extracted_text)
    try:
        parsed: ParsedCV = await parse_cv(cv_doc.extracted_text, language)
    except _TRANSIENT_ANTHROPIC as exc:
        await _merge_application_step_state(
            session,
            where=where_apps,
            step=CvPipelineStep.PARSE_CV,
            state=CvPipelineStepState.PENDING,
        )
        await session.commit()
        raise TransientCvPipelineError("Anthropic transient error during parse_cv") from exc
    except Exception as exc:
        await _merge_application_step_state(
            session,
            where=where_apps,
            step=CvPipelineStep.PARSE_CV,
            state=CvPipelineStepState.FAILED,
            error=str(exc),
        )
        await session.commit()
        logger.exception("cv_pipeline.parse.failed", extra={"cv_document_id": str(cv_document_id)})
        return

    candidate.parsed_profile = parsed.model_dump(mode="json")
    _reconcile_candidate_identity(candidate, parsed)
    await _merge_application_step_state(
        session, where=where_apps, step=CvPipelineStep.PARSE_CV, state=CvPipelineStepState.OK
    )
    await session.commit()
    logger.info(
        "cv_pipeline.parse.ok",
        extra={
            "cv_document_id": str(cv_document_id),
            "candidate_id": str(candidate.id),
            "parse_status": parsed.parse_status.value,
            "skill_count": len(parsed.skills),
            "work_count": len(parsed.work_history),
        },
    )


async def _score_authenticity(
    cv_text: str,
    parsed: ParsedCV,
    *,
    job_title: str | None = None,
    required_skills: list[str] | None = None,
) -> AuthenticityResult:
    """Run the LLM judge then aggregate into one :class:`AuthenticityResult`.

    Job-aware (six signals) when ``job_title`` is supplied, else the
    job-agnostic baseline (five signals). May raise a transient Anthropic
    error, which the caller converts into a Celery retry.
    """
    judge = await judge_authenticity(
        cv_text, parsed, job_title=job_title, required_skills=required_skills
    )
    return aggregate_authenticity(judge.signals, rationale=judge.rationale)


def _baseline_from(result: AuthenticityResult) -> AuthenticityResult:
    """Re-aggregate the five job-agnostic signals of a job-aware result.

    The candidate-level baseline is exactly the job-aware breakdown minus
    JD Keyword Mirroring, re-normalised — so one job-aware judge call yields
    both scores without a second Claude call.
    """
    subset = {
        key: signal for key, signal in result.breakdown.items() if key in BASELINE_SIGNAL_KEYS
    }
    return aggregate_authenticity(subset, rationale=result.rationale)


def _apply_authenticity(target: Application | Candidate, result: AuthenticityResult) -> None:
    """Persist an authenticity result onto an application or candidate row."""
    target.authenticity_score = result.score
    target.authenticity_band = result.band
    target.authenticity_breakdown = result.model_dump(mode="json")
    target.authenticity_computed_at = datetime.now(UTC)


async def _load_authenticity_inputs(
    session: AsyncSession,
    candidate_id: uuid.UUID,
    *,
    step_where: Any,
    log_ctx: dict[str, str],
) -> tuple[Candidate, str, ParsedCV] | None:
    """Load candidate + current CV text + parsed profile for authenticity.

    On any missing/invalid prerequisite, records ``FAILED`` on the
    authenticity step (scoped to ``step_where``), commits, and returns
    ``None`` — callers treat ``None`` as "stop, already recorded".
    """
    candidate = await _load_candidate(session, candidate_id)
    if candidate is None:
        logger.warning("cv_pipeline.authenticity.candidate_missing", extra=log_ctx)
        return None

    cv_doc = await _current_cv_for_candidate(session, candidate_id)
    if cv_doc is None or not cv_doc.extracted_text:
        await _merge_application_step_state(
            session,
            where=step_where,
            step=CvPipelineStep.AUTHENTICITY,
            state=CvPipelineStepState.FAILED,
            error="extracted_text_missing",
        )
        await session.commit()
        logger.warning("cv_pipeline.authenticity.no_text", extra=log_ctx)
        return None

    if not candidate.parsed_profile:
        await _merge_application_step_state(
            session,
            where=step_where,
            step=CvPipelineStep.AUTHENTICITY,
            state=CvPipelineStepState.FAILED,
            error="parsed_profile_missing",
        )
        await session.commit()
        logger.warning("cv_pipeline.authenticity.no_parse", extra=log_ctx)
        return None

    try:
        parsed = ParsedCV.model_validate(candidate.parsed_profile)
    except Exception as exc:
        await _merge_application_step_state(
            session,
            where=step_where,
            step=CvPipelineStep.AUTHENTICITY,
            state=CvPipelineStepState.FAILED,
            error=f"parsed_profile_invalid: {exc}",
        )
        await session.commit()
        return None

    return candidate, cv_doc.extracted_text, parsed


async def compute_application_authenticity(
    application_id: uuid.UUID, *, session: AsyncSession
) -> None:
    """Compute an application's job-aware authenticity (all six signals).

    Runs the six-signal, job-aware judge against the application's job,
    persists the verdict on the application, and — on the candidate's first
    scored application — derives the job-agnostic baseline from the same
    result and writes it to the candidate row (no extra Claude call).

    Idempotent: if the application's ``authenticity_computed_at`` is set,
    returns after marking the step ``OK``.
    """
    application = await _load_application(session, application_id)
    if application is None:
        logger.warning(
            "cv_pipeline.authenticity.app_missing", extra={"application_id": str(application_id)}
        )
        return

    where_app = Application.id == application_id
    log_ctx = {
        "application_id": str(application_id),
        "candidate_id": str(application.candidate_id),
    }

    if application.authenticity_computed_at is not None:
        await _merge_application_step_state(
            session,
            where=where_app,
            step=CvPipelineStep.AUTHENTICITY,
            state=CvPipelineStepState.OK,
        )
        await session.commit()
        logger.info("cv_pipeline.authenticity.skipped", extra=log_ctx)
        return

    loaded = await _load_authenticity_inputs(
        session, application.candidate_id, step_where=where_app, log_ctx=log_ctx
    )
    if loaded is None:
        return
    candidate, cv_text, parsed = loaded

    job = await session.get(Job, application.job_id)
    if job is None:
        await _merge_application_step_state(
            session,
            where=where_app,
            step=CvPipelineStep.AUTHENTICITY,
            state=CvPipelineStepState.FAILED,
            error="job_missing",
        )
        await session.commit()
        logger.warning("cv_pipeline.authenticity.no_job", extra=log_ctx)
        return

    try:
        result = await _score_authenticity(
            cv_text, parsed, job_title=job.title, required_skills=list(job.required_skills)
        )
    except _TRANSIENT_ANTHROPIC as exc:
        await _merge_application_step_state(
            session,
            where=where_app,
            step=CvPipelineStep.AUTHENTICITY,
            state=CvPipelineStepState.PENDING,
        )
        await session.commit()
        raise TransientCvPipelineError("Anthropic transient error during authenticity") from exc
    except Exception as exc:
        await _merge_application_step_state(
            session,
            where=where_app,
            step=CvPipelineStep.AUTHENTICITY,
            state=CvPipelineStepState.FAILED,
            error=str(exc),
        )
        await session.commit()
        logger.exception("cv_pipeline.authenticity.failed", extra=log_ctx)
        return

    _apply_authenticity(application, result)
    # The candidate's first scored application also seeds the job-agnostic
    # baseline (the talent-pool view). Later applications skip it: the baseline
    # is job-independent, so it's identical no matter which job computed it.
    if candidate.authenticity_computed_at is None:
        _apply_authenticity(candidate, _baseline_from(result))

    await _merge_application_step_state(
        session,
        where=where_app,
        step=CvPipelineStep.AUTHENTICITY,
        state=CvPipelineStepState.OK,
    )
    await session.commit()
    await publish_app_event(
        AppEvent(
            event=AppEventType.AUTHENTICITY,
            application_id=application.id,
            job_id=application.job_id,
            state=AppEventState.OK,
        )
    )
    logger.info(
        "cv_pipeline.authenticity.ok",
        extra={**log_ctx, "score": result.score, "band": result.band.value},
    )


async def compute_pool_authenticity(candidate_id: uuid.UUID, *, session: AsyncSession) -> None:
    """Compute a pooled candidate's job-agnostic baseline (five signals).

    Used by the talent-pool CV chain, which has no application/job to score
    against. Persists the baseline on the candidate row. No SSE event — a
    pooled candidate has no board card to nudge (and any existing
    applications carry their own job-aware score).

    Idempotent: if the candidate's ``authenticity_computed_at`` is set,
    returns after marking the step ``OK``.
    """
    where_cand = Application.candidate_id == candidate_id
    log_ctx = {"candidate_id": str(candidate_id)}

    candidate = await _load_candidate(session, candidate_id)
    if candidate is None:
        logger.warning("cv_pipeline.authenticity.candidate_missing", extra=log_ctx)
        return

    if candidate.authenticity_computed_at is not None:
        await _merge_application_step_state(
            session,
            where=where_cand,
            step=CvPipelineStep.AUTHENTICITY,
            state=CvPipelineStepState.OK,
        )
        await session.commit()
        logger.info("cv_pipeline.authenticity.skipped", extra=log_ctx)
        return

    loaded = await _load_authenticity_inputs(
        session, candidate_id, step_where=where_cand, log_ctx=log_ctx
    )
    if loaded is None:
        return
    _candidate, cv_text, parsed = loaded

    try:
        result = await _score_authenticity(cv_text, parsed)
    except _TRANSIENT_ANTHROPIC as exc:
        await _merge_application_step_state(
            session,
            where=where_cand,
            step=CvPipelineStep.AUTHENTICITY,
            state=CvPipelineStepState.PENDING,
        )
        await session.commit()
        raise TransientCvPipelineError("Anthropic transient error during authenticity") from exc
    except Exception as exc:
        await _merge_application_step_state(
            session,
            where=where_cand,
            step=CvPipelineStep.AUTHENTICITY,
            state=CvPipelineStepState.FAILED,
            error=str(exc),
        )
        await session.commit()
        logger.exception("cv_pipeline.authenticity.failed", extra=log_ctx)
        return

    _apply_authenticity(_candidate, result)
    await _merge_application_step_state(
        session,
        where=where_cand,
        step=CvPipelineStep.AUTHENTICITY,
        state=CvPipelineStepState.OK,
    )
    await session.commit()
    logger.info(
        "cv_pipeline.authenticity.ok",
        extra={**log_ctx, "score": result.score, "band": result.band.value},
    )


async def embed_cv(cv_document_id: uuid.UUID, *, session: AsyncSession) -> None:
    """Embed the CV text and persist to ``cv_documents.cv_embedding``.

    Idempotent: if the embedding is already populated, returns immediately.
    """
    cv_doc = await _load_cv_document(session, cv_document_id)
    if cv_doc is None:
        logger.warning(
            "cv_pipeline.embed.cv_missing", extra={"cv_document_id": str(cv_document_id)}
        )
        return

    where_apps = Application.cv_document_id == cv_document_id

    if cv_doc.cv_embedding is not None:
        await _merge_application_step_state(
            session, where=where_apps, step=CvPipelineStep.EMBED_CV, state=CvPipelineStepState.OK
        )
        await session.commit()
        logger.info("cv_pipeline.embed.skipped", extra={"cv_document_id": str(cv_document_id)})
        return

    if not cv_doc.extracted_text:
        await _merge_application_step_state(
            session,
            where=where_apps,
            step=CvPipelineStep.EMBED_CV,
            state=CvPipelineStepState.FAILED,
            error="extracted_text_missing",
        )
        await session.commit()
        logger.warning("cv_pipeline.embed.no_text", extra={"cv_document_id": str(cv_document_id)})
        return

    try:
        vector = await embed_text(cv_doc.extracted_text)
    except _TRANSIENT_OPENAI as exc:
        await _merge_application_step_state(
            session,
            where=where_apps,
            step=CvPipelineStep.EMBED_CV,
            state=CvPipelineStepState.PENDING,
        )
        await session.commit()
        raise TransientCvPipelineError("OpenAI transient error during embed_cv") from exc
    except Exception as exc:
        await _merge_application_step_state(
            session,
            where=where_apps,
            step=CvPipelineStep.EMBED_CV,
            state=CvPipelineStepState.FAILED,
            error=str(exc),
        )
        await session.commit()
        logger.exception("cv_pipeline.embed.failed", extra={"cv_document_id": str(cv_document_id)})
        return

    cv_doc.cv_embedding = vector
    await _merge_application_step_state(
        session, where=where_apps, step=CvPipelineStep.EMBED_CV, state=CvPipelineStepState.OK
    )
    await session.commit()
    logger.info("cv_pipeline.embed.ok", extra={"cv_document_id": str(cv_document_id)})


async def score_similarity(
    application_id: uuid.UUID, *, session: AsyncSession, force: bool = False
) -> None:
    """Compute cosine similarity between CV and JD embeddings; persist on the application.

    Idempotent: if ``similarity_score`` is already populated, returns
    immediately after marking ``OK``. Missing embeddings on either side
    -> ``FAILED`` (the chain is expected to have populated them upstream).

    When ``force=True`` (Step 4.4 rescore path) the idempotent-skip on
    an existing ``similarity_score`` is bypassed and the existing value
    is nulled before the run so the detail endpoint reflects "in
    progress" instead of the stale value.
    """
    application = await _load_application(session, application_id)
    if application is None:
        logger.warning(
            "cv_pipeline.similarity.app_missing", extra={"application_id": str(application_id)}
        )
        return

    if not force and application.similarity_score is not None:
        await _merge_application_step_state(
            session,
            where=Application.id == application_id,
            step=CvPipelineStep.SIMILARITY,
            state=CvPipelineStepState.OK,
        )
        await session.commit()
        logger.info("cv_pipeline.similarity.skipped", extra={"application_id": str(application_id)})
        return

    if force and application.similarity_score is not None:
        # Null the denormalised score so the detail endpoint reflects
        # "scoring in progress" instead of the stale value. Audit
        # history in ``application_scores`` retains the prior attempt.
        application.similarity_score = None
        await session.flush()

    cv_doc = await _load_cv_document(session, application.cv_document_id)
    job = await session.get(Job, application.job_id)
    if cv_doc is None or cv_doc.cv_embedding is None:
        await _merge_application_step_state(
            session,
            where=Application.id == application_id,
            step=CvPipelineStep.SIMILARITY,
            state=CvPipelineStepState.FAILED,
            error="cv_embedding_missing",
        )
        await session.commit()
        logger.warning(
            "cv_pipeline.similarity.no_cv_embedding",
            extra={"application_id": str(application_id)},
        )
        return
    if job is None or job.jd_embedding is None:
        await _merge_application_step_state(
            session,
            where=Application.id == application_id,
            step=CvPipelineStep.SIMILARITY,
            state=CvPipelineStepState.FAILED,
            error="jd_embedding_missing",
        )
        await session.commit()
        logger.warning(
            "cv_pipeline.similarity.no_jd_embedding",
            extra={"application_id": str(application_id)},
        )
        return

    score = _cosine_similarity(list(cv_doc.cv_embedding), list(job.jd_embedding))
    application.similarity_score = score

    # Structured breakdown — JD required/preferred skill coverage + the
    # threshold context — so the detail endpoint can show HR *why* the
    # auto-rejector did (or didn't) fire. Tolerates a candidate whose
    # parse step ran but produced no ``skills`` key (treated as empty).
    candidate = await _load_candidate(session, application.candidate_id)
    cv_skills_raw = candidate.parsed_profile.get("skills", []) if candidate is not None else []
    cv_skills: list[str] = [s for s in cv_skills_raw if isinstance(s, str)]
    breakdown = compute_similarity_breakdown(
        similarity_score=score,
        required_skills=list(job.required_skills or []),
        preferred_skills=list(job.preferred_skills or []),
        cv_skills=cv_skills,
    )

    # Per Step 4.1: append an audit row to ``application_scores`` so the
    # detail endpoint surfaces the score history. The denormalised
    # ``Application.similarity_score`` above is the latest value used for
    # fast list-sort; this row is the immutable record of the attempt.
    session.add(
        ApplicationScore(
            application_id=application_id,
            score_type=ScoreType.SIMILARITY,
            value=score,
            breakdown=breakdown,
            prompt_version="-",
            model_used=ScoreModel.OPENAI_EMBED_3_SMALL.value,
        )
    )
    await _merge_application_step_state(
        session,
        where=Application.id == application_id,
        step=CvPipelineStep.SIMILARITY,
        state=CvPipelineStepState.OK,
    )
    await session.commit()
    await publish_app_event(
        AppEvent(
            event=AppEventType.SIMILARITY,
            application_id=application_id,
            job_id=application.job_id,
            state=AppEventState.OK,
        )
    )
    logger.info(
        "cv_pipeline.similarity.ok",
        extra={"application_id": str(application_id), "similarity": round(score, 4)},
    )


async def auto_reject_below_threshold(application_id: uuid.UUID, *, session: AsyncSession) -> None:
    """Flip ``status`` to REJECTED when similarity is below the threshold.

    Stage stays at ``VECTOR_SCREEN`` so HR still sees *why* the
    application was dropped (per doc Step 3.9 acceptance).

    Idempotent: re-running on an already-REJECTED row is a no-op; same
    for above-threshold rows -- both end with the step marked
    ``SKIPPED`` or ``OK`` as appropriate.

    Compares cosine *distance* (``100 - similarity``, on the 0-100 scale)
    against :data:`SIMILARITY_REJECTION_THRESHOLD` so the constant's
    existing "distance threshold" semantics carry over cleanly.
    """
    application = await _load_application(session, application_id)
    if application is None:
        logger.warning(
            "cv_pipeline.auto_reject.app_missing",
            extra={"application_id": str(application_id)},
        )
        return

    if application.similarity_score is None:
        await _merge_application_step_state(
            session,
            where=Application.id == application_id,
            step=CvPipelineStep.AUTO_REJECT,
            state=CvPipelineStepState.FAILED,
            error="similarity_score_missing",
        )
        await session.commit()
        logger.warning(
            "cv_pipeline.auto_reject.no_score",
            extra={"application_id": str(application_id)},
        )
        return

    distance = 100.0 - application.similarity_score
    if distance > SIMILARITY_REJECTION_THRESHOLD:
        application.status = ApplicationStatus.REJECTED
        await _merge_application_step_state(
            session,
            where=Application.id == application_id,
            step=CvPipelineStep.AUTO_REJECT,
            state=CvPipelineStepState.OK,
        )
        await session.commit()
        logger.info(
            "cv_pipeline.auto_reject.rejected",
            extra={
                "application_id": str(application_id),
                "similarity": round(application.similarity_score, 4),
                "distance": round(distance, 4),
                "threshold": SIMILARITY_REJECTION_THRESHOLD,
            },
        )
        return

    await _merge_application_step_state(
        session,
        where=Application.id == application_id,
        step=CvPipelineStep.AUTO_REJECT,
        state=CvPipelineStepState.SKIPPED,
    )
    await session.commit()
    logger.info(
        "cv_pipeline.auto_reject.kept",
        extra={
            "application_id": str(application_id),
            "similarity": round(application.similarity_score, 4),
            "distance": round(distance, 4),
            "threshold": SIMILARITY_REJECTION_THRESHOLD,
        },
    )


# --- Sync Celery wrappers ---------------------------------------------------


async def _run_with_session(coro_factory: Any, arg: uuid.UUID) -> None:
    """Open a fresh AsyncSession, run the helper, close. One transaction."""
    async with SessionFactory() as session:
        await coro_factory(arg, session=session)


async def _run_with_session_force(coro_factory: Any, arg: uuid.UUID, *, force: bool) -> None:
    """Variant for helpers that accept the Step 4.4 ``force`` kwarg."""
    async with SessionFactory() as session:
        await coro_factory(arg, session=session, force=force)


# Celery's ``.task`` decorator returns an untyped ``Task`` subclass; the
# stubs aren't generic over the wrapped signature so strict mode flags
# every task. Same pattern as job_pipeline -- silence per-task.
@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.cv_extract_text",
    queue=SLOW_QUEUE,
    autoretry_for=(TransientCvPipelineError,),
    retry_kwargs={
        "max_retries": CV_PIPELINE_MAX_RETRIES,
        "countdown": CV_PIPELINE_RETRY_BACKOFF_SECONDS,
    },
    retry_backoff=True,
    retry_backoff_max=CV_PIPELINE_RETRY_BACKOFF_SECONDS * 4,
    retry_jitter=False,
)
def extract_text_task(cv_document_id: str) -> None:
    """Celery shim: bridge sync->async and own session lifetime."""
    asyncio.run(_run_with_session(extract_cv_text, uuid.UUID(cv_document_id)))


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.cv_parse",
    queue=SLOW_QUEUE,
    autoretry_for=(TransientCvPipelineError,),
    retry_kwargs={
        "max_retries": CV_PIPELINE_MAX_RETRIES,
        "countdown": CV_PIPELINE_RETRY_BACKOFF_SECONDS,
    },
    retry_backoff=True,
    retry_backoff_max=CV_PIPELINE_RETRY_BACKOFF_SECONDS * 4,
    retry_jitter=False,
)
def parse_cv_task(cv_document_id: str) -> None:
    asyncio.run(_run_with_session(parse_cv_for_document, uuid.UUID(cv_document_id)))


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.cv_authenticity",
    queue=SLOW_QUEUE,
    autoretry_for=(TransientCvPipelineError,),
    retry_kwargs={
        "max_retries": CV_PIPELINE_MAX_RETRIES,
        "countdown": CV_PIPELINE_RETRY_BACKOFF_SECONDS,
    },
    retry_backoff=True,
    retry_backoff_max=CV_PIPELINE_RETRY_BACKOFF_SECONDS * 4,
    retry_jitter=False,
)
def compute_application_authenticity_task(application_id: str) -> None:
    asyncio.run(_run_with_session(compute_application_authenticity, uuid.UUID(application_id)))


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.cv_pool_authenticity",
    queue=SLOW_QUEUE,
    autoretry_for=(TransientCvPipelineError,),
    retry_kwargs={
        "max_retries": CV_PIPELINE_MAX_RETRIES,
        "countdown": CV_PIPELINE_RETRY_BACKOFF_SECONDS,
    },
    retry_backoff=True,
    retry_backoff_max=CV_PIPELINE_RETRY_BACKOFF_SECONDS * 4,
    retry_jitter=False,
)
def compute_pool_authenticity_task(candidate_id: str) -> None:
    asyncio.run(_run_with_session(compute_pool_authenticity, uuid.UUID(candidate_id)))


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.cv_embed",
    queue=SLOW_QUEUE,
    autoretry_for=(TransientCvPipelineError,),
    retry_kwargs={
        "max_retries": CV_EMBEDDING_MAX_RETRIES,
        "countdown": CV_PIPELINE_RETRY_BACKOFF_SECONDS,
    },
    retry_backoff=True,
    retry_backoff_max=CV_PIPELINE_RETRY_BACKOFF_SECONDS * 4,
    retry_jitter=False,
)
def embed_cv_task(cv_document_id: str) -> None:
    asyncio.run(_run_with_session(embed_cv, uuid.UUID(cv_document_id)))


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.cv_score_similarity",
    queue=SLOW_QUEUE,
    autoretry_for=(TransientCvPipelineError,),
    retry_kwargs={
        "max_retries": CV_PIPELINE_MAX_RETRIES,
        "countdown": CV_PIPELINE_RETRY_BACKOFF_SECONDS,
    },
    retry_backoff=True,
    retry_backoff_max=CV_PIPELINE_RETRY_BACKOFF_SECONDS * 4,
    retry_jitter=False,
)
def score_similarity_task(application_id: str, force: bool = False) -> None:
    """Compute cosine similarity for an application.

    ``force=True`` is the Step 4.4 rescore path: bypasses the
    idempotent-skip on existing scores. No Claude cache to bypass here
    (similarity is pure vector math against already-cached embeddings).
    """
    asyncio.run(_run_with_session_force(score_similarity, uuid.UUID(application_id), force=force))


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.cv_auto_reject",
    queue=SLOW_QUEUE,
    autoretry_for=(TransientCvPipelineError,),
    retry_kwargs={
        "max_retries": CV_PIPELINE_MAX_RETRIES,
        "countdown": CV_PIPELINE_RETRY_BACKOFF_SECONDS,
    },
    retry_backoff=True,
    retry_backoff_max=CV_PIPELINE_RETRY_BACKOFF_SECONDS * 4,
    retry_jitter=False,
)
def auto_reject_below_threshold_task(application_id: str) -> None:
    asyncio.run(_run_with_session(auto_reject_below_threshold, uuid.UUID(application_id)))


# --- Dispatcher -------------------------------------------------------------


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.dispatch_cv_processing",
    queue=SLOW_QUEUE,
)
def dispatch_cv_processing_task(application_id: str) -> None:
    """Compose and dispatch the six-task CV processing chain.

    Resolves the application's candidate + CV ids in a tiny synchronous
    session so we can build the chain with the right argument per task
    (extract / parse / embed take cv_document_id; authenticity, similarity,
    and auto_reject take application_id — job-aware authenticity needs the
    application to reach its job).

    Uses ``celery.chain`` with ``.si()`` (immutable) signatures so no
    upstream return value leaks into the next task -- each task receives
    only the id it was constructed with.
    """
    from celery import chain  # local import: avoid circular-by-name with celery_app

    asyncio.run(_dispatch_chain(uuid.UUID(application_id), chain=chain))


async def _dispatch_chain(application_id: uuid.UUID, *, chain: Any) -> None:
    async with SessionFactory() as session:
        application = await _load_application(session, application_id)
        if application is None:
            logger.warning(
                "cv_pipeline.dispatch.app_missing",
                extra={"application_id": str(application_id)},
            )
            return
        cv_document_id = application.cv_document_id
        candidate_id = application.candidate_id

    pipeline = chain(
        extract_text_task.si(str(cv_document_id)),
        parse_cv_task.si(str(cv_document_id)),
        compute_application_authenticity_task.si(str(application_id)),
        embed_cv_task.si(str(cv_document_id)),
        score_similarity_task.si(str(application_id)),
        auto_reject_below_threshold_task.si(str(application_id)),
    )
    pipeline.apply_async()
    logger.info(
        "cv_pipeline.dispatch.ok",
        extra={
            "application_id": str(application_id),
            "candidate_id": str(candidate_id),
            "cv_document_id": str(cv_document_id),
        },
    )


# --- Talent-pool dispatcher (Phase 7: direct CV upload) ---------------------
#
# A direct talent-pool upload has no Application and no Job, so the
# application-keyed tail of the full chain (similarity + auto_reject) cannot
# run — there's nothing to score against. We run only the candidate/CV-keyed
# prefix so the CV is parsed, authenticity-scored, and (crucially) embedded,
# which is what semantic pool search ranks on. The shared tasks' internal
# ``_merge_application_step_state`` updates match zero application rows here,
# which is harmless: the real work persists on cv_documents / candidates.


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.dispatch_pool_cv_processing",
    queue=SLOW_QUEUE,
)
def dispatch_pool_cv_processing_task(cv_document_id: str, candidate_id: str) -> None:
    """Compose and dispatch the pool CV-processing chain (no scoring tail).

    Chains ``extract_text -> parse_cv -> authenticity -> embed_cv`` so a
    pooled candidate's CV is fully processed and searchable. Unlike the
    application dispatcher this takes the ids directly (no Application row to
    resolve them from).
    """
    from celery import chain  # local import: avoid circular-by-name with celery_app

    pipeline = chain(
        extract_text_task.si(cv_document_id),
        parse_cv_task.si(cv_document_id),
        compute_pool_authenticity_task.si(candidate_id),
        embed_cv_task.si(cv_document_id),
    )
    pipeline.apply_async()
    logger.info(
        "cv_pipeline.dispatch_pool.ok",
        extra={"cv_document_id": cv_document_id, "candidate_id": candidate_id},
    )
