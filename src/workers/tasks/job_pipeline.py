"""Job background pipeline.

Two tasks fire on a ``draft → open`` (or ``closed → open``) transition:

* :func:`embed_jd_task` — embeds the JD and writes the 1536-d vector to
  ``Job.jd_embedding``.
* :func:`generate_questions_task` — generates the bilingual WhatsApp
  screening questions and writes them to ``Job.whatsapp_questions``.

The split between *async helpers* and *sync task wrappers* is
deliberate: the helpers do the real work and take a session, so tests
can drive them directly against the per-test transactional session
fixture; the wrappers exist purely to bridge Celery's sync execution
model to our async stack via :func:`asyncio.run`.

Both helpers are idempotent — calling them again after success is a
cheap no-op. That makes Celery's at-least-once delivery safe and lets
``closed → open → closed → open`` cycles fire without duplicating work.

Non-transient failures (validation, missing job, etc.) update
``pipeline_status[step] = 'failed'`` plus a parallel ``{step}_error``
key, then *don't* retry. Transient OpenAI / Anthropic errors are
re-raised as :class:`TransientPipelineError` so Celery's auto-retry
kicks in with exponential backoff.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Final, cast

import anthropic
import openai
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.embeddings import embed_text
from src.ai.whatsapp_question_generator import generate_whatsapp_questions
from src.db.models.job import Job
from src.db.session import SessionFactory
from src.enums.pipeline import PipelineStep, PipelineStepState
from src.workers.celery_app import SLOW_QUEUE, celery_app

logger = logging.getLogger(__name__)

# --- Retry constants --------------------------------------------------------

# Celery retry knobs. Exponential-ish: 60, 120, 240 seconds. Three retries
# is enough to ride out a ~10 min transient outage at the provider end.
PIPELINE_MAX_RETRIES: Final[int] = 3
PIPELINE_RETRY_BACKOFF_SECONDS: Final[int] = 60

# Provider-side exception classes we treat as worth retrying. Anything
# else (validation, schema, missing row) is permanent.
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


class TransientPipelineError(RuntimeError):
    """Raised inside a helper to signal "Celery should retry me later".

    The wrapped provider exception lives on ``__cause__`` for log triage.
    """


# --- Atomic status-mutation helpers -----------------------------------------
#
# ``pipeline_status`` is touched concurrently by ``embed_jd_task`` and
# ``generate_questions_task`` (they fan out in parallel on every
# ``draft -> open`` transition). A Python-side read-modify-write loses
# whichever task commits second: it loads the row before the first
# task's commit, so its dict snapshot misses the first task's key, and
# its UPDATE overwrites the merged value with its own narrower one.
#
# We avoid the race by doing the merge *in SQL*: ``UPDATE jobs SET
# pipeline_status = (pipeline_status - error_key) || patch::jsonb``.
# Postgres takes a row lock during the UPDATE so two concurrent merges
# serialize -- the second task's UPDATE reads the just-committed value
# from the first and appends its own keys on top. No clobbering.


async def _merge_step_state(
    session: AsyncSession,
    job_id: uuid.UUID,
    step: PipelineStep,
    state: PipelineStepState,
    *,
    error: str | None = None,
) -> dict[str, Any] | None:
    """Atomically merge ``{step: state}`` into ``Job.pipeline_status``.

    Implementation: ``pipeline_status - error_key || patch::jsonb`` runs
    server-side, holding a row lock for the duration so concurrent
    callers serialize at the DB layer rather than clobbering each
    other's keys at the Python layer.

    Returns the fresh ``pipeline_status`` dict from the RETURNING clause
    so :func:`_set_ready_for_applications` can decide the ready flag in
    the same transaction. Returns ``None`` if the row vanished between
    the caller's ``_load_job`` and this UPDATE.
    """
    error_key = f"{step.value}_error"
    patch: dict[str, str] = {step.value: state.value}
    if state is PipelineStepState.FAILED and error is not None:
        patch[error_key] = error

    # ``sa.literal(json.dumps(patch))`` plus ``cast(..., JSONB)`` is the
    # portable way to bind a Python dict as a JSONB value inside a SQL
    # expression. ``sa.cast(patch, JSONB)`` alone doesn't work -- cast
    # operates on SQL expressions, not Python values.
    patch_jsonb = sa.cast(sa.literal(json.dumps(patch)), JSONB)

    stmt = (
        sa.update(Job)
        .where(Job.id == job_id)
        .values(pipeline_status=Job.pipeline_status.op("-")(error_key).op("||")(patch_jsonb))
        .returning(Job.pipeline_status)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return cast(dict[str, Any], row) if row is not None else None


async def _set_ready_for_applications(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    new_status: dict[str, Any],
) -> None:
    """Flip ``Job.ready_for_applications`` based on the merged pipeline_status."""
    all_ok = all(new_status.get(step.value) == PipelineStepState.OK.value for step in PipelineStep)
    await session.execute(
        sa.update(Job).where(Job.id == job_id).values(ready_for_applications=all_ok)
    )


# --- Async helpers (real work) ----------------------------------------------


async def _load_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    return await session.get(Job, job_id)


async def embed_jd(job_id: uuid.UUID, *, session: AsyncSession) -> None:
    """Embed the JD and persist to ``Job.jd_embedding``.

    Idempotent — if ``jd_embedding`` is already populated, returns
    immediately after marking the step ``OK`` (so a no-op rerun still
    reconciles ``pipeline_status`` with reality).

    Raises:
        TransientPipelineError: when OpenAI returns a retriable error.
            Celery will retry up to ``PIPELINE_MAX_RETRIES`` times.
    """
    job = await _load_job(session, job_id)
    if job is None:
        logger.warning("job_pipeline.embed.job_missing", extra={"job_id": str(job_id)})
        return
    if job.jd_embedding is not None:
        # Idempotent skip — still reconcile pipeline_status in case a
        # prior run failed to record OK.
        new_status = await _merge_step_state(
            session, job_id, PipelineStep.EMBEDDING, PipelineStepState.OK
        )
        if new_status is not None:
            await _set_ready_for_applications(session, job_id, new_status=new_status)
        await session.commit()
        logger.info("job_pipeline.embed.skipped", extra={"job_id": str(job_id)})
        return

    try:
        vector = await embed_text(job.job_description)
    except _TRANSIENT_OPENAI as exc:
        # Mark pending — leave the door open for a successful retry to
        # flip it to ``ok``. We persist this state so HR can see "we're
        # still trying" rather than a stale empty field.
        await _merge_step_state(session, job_id, PipelineStep.EMBEDDING, PipelineStepState.PENDING)
        await session.commit()
        raise TransientPipelineError("OpenAI transient error during embedding") from exc
    except Exception as exc:
        new_status = await _merge_step_state(
            session,
            job_id,
            PipelineStep.EMBEDDING,
            PipelineStepState.FAILED,
            error=str(exc),
        )
        if new_status is not None:
            await _set_ready_for_applications(session, job_id, new_status=new_status)
        await session.commit()
        logger.exception("job_pipeline.embed.failed", extra={"job_id": str(job_id)})
        return

    job.jd_embedding = vector
    new_status = await _merge_step_state(
        session, job_id, PipelineStep.EMBEDDING, PipelineStepState.OK
    )
    if new_status is not None:
        await _set_ready_for_applications(session, job_id, new_status=new_status)
    await session.commit()
    logger.info("job_pipeline.embed.ok", extra={"job_id": str(job_id)})


async def generate_questions_for_job(job_id: uuid.UUID, *, session: AsyncSession) -> None:
    """Generate WhatsApp screening questions and persist them.

    Idempotent — if ``whatsapp_questions`` is already non-empty, returns
    after reconciling ``pipeline_status``.

    Raises:
        TransientPipelineError: when Anthropic returns a retriable error.
    """
    job = await _load_job(session, job_id)
    if job is None:
        logger.warning("job_pipeline.questions.job_missing", extra={"job_id": str(job_id)})
        return
    if job.whatsapp_questions:
        new_status = await _merge_step_state(
            session, job_id, PipelineStep.QUESTIONS, PipelineStepState.OK
        )
        if new_status is not None:
            await _set_ready_for_applications(session, job_id, new_status=new_status)
        await session.commit()
        logger.info("job_pipeline.questions.skipped", extra={"job_id": str(job_id)})
        return

    try:
        questions = await generate_whatsapp_questions(job=job)
    except _TRANSIENT_ANTHROPIC as exc:
        await _merge_step_state(session, job_id, PipelineStep.QUESTIONS, PipelineStepState.PENDING)
        await session.commit()
        raise TransientPipelineError("Anthropic transient error during question gen") from exc
    except Exception as exc:
        new_status = await _merge_step_state(
            session,
            job_id,
            PipelineStep.QUESTIONS,
            PipelineStepState.FAILED,
            error=str(exc),
        )
        if new_status is not None:
            await _set_ready_for_applications(session, job_id, new_status=new_status)
        await session.commit()
        logger.exception("job_pipeline.questions.failed", extra={"job_id": str(job_id)})
        return

    job.whatsapp_questions = [q.model_dump(mode="json") for q in questions]
    new_status = await _merge_step_state(
        session, job_id, PipelineStep.QUESTIONS, PipelineStepState.OK
    )
    if new_status is not None:
        await _set_ready_for_applications(session, job_id, new_status=new_status)
    await session.commit()
    logger.info(
        "job_pipeline.questions.ok",
        extra={"job_id": str(job_id), "count": len(questions)},
    )


# --- Sync Celery wrappers ---------------------------------------------------


async def _run_with_session(coro_factory: Any, job_id: uuid.UUID) -> None:
    """Open a fresh AsyncSession, run the helper, close. One transaction."""
    async with SessionFactory() as session:
        await coro_factory(job_id, session=session)


# Note: Celery's ``.task`` decorator returns an untyped ``Task`` subclass.
# The stubs aren't generic over the wrapped signature so strict mode flags
# every task. Silencing per-line keeps the rest of the module under --strict.
@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.embed_jd",
    queue=SLOW_QUEUE,
    autoretry_for=(TransientPipelineError,),
    retry_kwargs={"max_retries": PIPELINE_MAX_RETRIES, "countdown": PIPELINE_RETRY_BACKOFF_SECONDS},
    retry_backoff=True,
    retry_backoff_max=PIPELINE_RETRY_BACKOFF_SECONDS * 4,
    retry_jitter=False,
)
def embed_jd_task(job_id: str) -> None:
    """Celery shim: bridge sync→async and own session lifetime."""
    asyncio.run(_run_with_session(embed_jd, uuid.UUID(job_id)))


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.generate_questions",
    queue=SLOW_QUEUE,
    autoretry_for=(TransientPipelineError,),
    retry_kwargs={"max_retries": PIPELINE_MAX_RETRIES, "countdown": PIPELINE_RETRY_BACKOFF_SECONDS},
    retry_backoff=True,
    retry_backoff_max=PIPELINE_RETRY_BACKOFF_SECONDS * 4,
    retry_jitter=False,
)
def generate_questions_task(job_id: str) -> None:
    """Celery shim for the question generator."""
    asyncio.run(_run_with_session(generate_questions_for_job, uuid.UUID(job_id)))
