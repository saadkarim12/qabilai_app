"""Hard-filter scoring Celery task (Step 4.3).

Real implementation replacing the Step 4.2 stub. The task:

1. Loads the application + linked candidate (for ``parsed_profile``) +
   CV document (for ``extracted_text``) + job.
2. Marks ``pipeline_status['hard_filter'] = 'pending'`` so the detail
   endpoint shows "in progress" immediately, even before Claude
   responds.
3. Idempotent skip — if ``hard_filter_score`` is already populated,
   marks the step ``ok`` and returns. Cheap re-delivery handling.
4. Calls :func:`src.services.scoring.hard_filter_service.score_hard_filters`.
   Transient Anthropic errors propagate up to the Celery retry path
   (``autoretry_for``); content failures
   (:class:`HardFilterScoringError`) are caught and recorded as a
   ``failed`` pipeline step.
5. On success: writes
   * ``applications.hard_filter_score`` (weighted total),
   * ``applications.hard_filter_breakdown`` (per-signal detail),
   * a new ``application_scores`` audit row,
   * ``pipeline_status['hard_filter'] = 'ok'``.

Stage and status are intentionally NOT modified — the doc decision is
that HR advances stage manually via PATCH after reviewing the score.

CV text and Claude responses are never logged; log records carry only
ids, model name, and the aggregate score.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Final

import anthropic
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.application import Application
from src.db.models.application_score import ApplicationScore
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.job import Job
from src.db.session import SessionFactory
from src.enums.events import AppEventState, AppEventType
from src.enums.score_types import ScoreType
from src.schemas.events import AppEvent
from src.schemas.hard_filter import HardFilterScoreResult
from src.schemas.parsed_cv import ParsedCV
from src.services.events_service import publish_app_event
from src.services.scoring.hard_filter_service import (
    HardFilterScoringError,
    score_hard_filters,
)
from src.workers.celery_app import SLOW_QUEUE, celery_app

logger = logging.getLogger(__name__)

# --- Retry constants --------------------------------------------------------

HARD_FILTER_MAX_RETRIES: Final[int] = 3
HARD_FILTER_RETRY_BACKOFF_SECONDS: Final[int] = 60

# Transient Anthropic errors we want Celery to retry. Content failures
# (validation, schema) are not in this set — those raise
# :class:`HardFilterScoringError` which we catch and record permanently.
_TRANSIENT_ANTHROPIC: Final[tuple[type[Exception], ...]] = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)


class TransientHardFilterError(RuntimeError):
    """Raised inside the helper to signal "Celery should retry me later"."""


# --- pipeline_status keys ---------------------------------------------------

HARD_FILTER_STEP_KEY: Final[str] = "hard_filter"
HARD_FILTER_ERROR_KEY: Final[str] = "hard_filter_error"


# --- Atomic JSONB merge -----------------------------------------------------


async def _merge_pipeline_status(
    session: AsyncSession,
    application_id: uuid.UUID,
    *,
    state: str,
    error: str | None = None,
) -> None:
    """Atomically merge ``{hard_filter: state}`` into pipeline_status.

    Same SQL pattern as :mod:`src.workers.tasks.cv_pipeline` — server-side
    merge so concurrent CV-pipeline writes to the same JSONB blob don't
    clobber each other.
    """
    patch: dict[str, str] = {HARD_FILTER_STEP_KEY: state}
    if error is not None and state == "failed":
        patch[HARD_FILTER_ERROR_KEY] = error
    patch_jsonb = sa.cast(sa.literal(json.dumps(patch)), JSONB)
    stmt = (
        sa.update(Application)
        .where(Application.id == application_id)
        .values(
            pipeline_status=Application.pipeline_status.op("-")(HARD_FILTER_ERROR_KEY).op("||")(
                patch_jsonb
            )
        )
    )
    await session.execute(stmt)


# --- Loaders ----------------------------------------------------------------


async def _load_application(session: AsyncSession, application_id: uuid.UUID) -> Application | None:
    return await session.get(Application, application_id)


# --- Realtime nudge ---------------------------------------------------------


async def _publish_hard_filter_event(application: Application, *, state: AppEventState) -> None:
    """Best-effort realtime nudge after a terminal hard-filter write.

    Called *after* the DB commit so a refetching FE sees the committed score.
    Both inputs come off the already-loaded application row. Any publish
    failure is swallowed inside :func:`publish_app_event` — it must never
    break scoring.
    """
    await publish_app_event(
        AppEvent(
            event=AppEventType.HARD_FILTER,
            application_id=application.id,
            job_id=application.job_id,
            state=state,
        )
    )


# --- Helpers ----------------------------------------------------------------


def _write_audit_row(
    session: AsyncSession,
    *,
    application_id: uuid.UUID,
    result: HardFilterScoreResult,
) -> None:
    """Append an :class:`ApplicationScore` row for the audit trail.

    Stored breakdown is the model_dump of the result's signals dict so
    HR can see each signal's score + reasoning + weight after the fact.
    """
    breakdown_payload = {
        "signals": {
            key.value: detail.model_dump(mode="json") for key, detail in result.signals.items()
        }
    }
    session.add(
        ApplicationScore(
            application_id=application_id,
            score_type=ScoreType.HARD_FILTER,
            value=result.total,
            breakdown=breakdown_payload,
            prompt_version=result.prompt_version,
            model_used=result.model_used,
        )
    )


def _serialise_breakdown(result: HardFilterScoreResult) -> dict[str, dict[str, float | str]]:
    """Shape for ``applications.hard_filter_breakdown`` JSONB column.

    Same shape as the audit-row's ``signals`` payload so the two stay
    legible together. The top level is the signal key; the value is
    {score, reasoning, weight}.
    """
    return {key.value: detail.model_dump(mode="json") for key, detail in result.signals.items()}


# --- Async helper (real work, session injected) -----------------------------
#
# The split between this helper (takes a session) and ``_run_with_session``
# (opens a fresh AsyncSession) mirrors the pattern used in
# :mod:`src.workers.tasks.cv_pipeline`: helpers are testable directly
# with the per-test transactional session fixture, wrappers bridge
# Celery's sync execution model to async.


async def score_hard_filter_for_application(
    application_id: uuid.UUID, *, session: AsyncSession, force: bool = False
) -> None:
    """Score the application and persist the result.

    Mark pending → check idempotency → call Claude → persist → mark ok.
    On content failure, mark failed + error. On transient Anthropic
    failure, mark pending and raise :class:`TransientHardFilterError`
    so the Celery wrapper's autoretry kicks in.

    When ``force=True`` (Step 4.4 rescore path):
      * the idempotent-skip on an existing ``hard_filter_score`` is
        bypassed,
      * the existing denormalised score + breakdown are nulled before
        the run so the detail endpoint shows "in progress" instead of
        the stale value,
      * the underlying Claude call bypasses its Redis cache so HR
        actually gets a fresh response.
    """
    application = await _load_application(session, application_id)
    if application is None:
        logger.warning(
            "scoring.hard_filter.app_missing",
            extra={"application_id": str(application_id)},
        )
        return

    # Idempotent skip — re-delivery of an already-scored row just
    # reconciles pipeline_status. Skipped on the rescore path so HR
    # gets a fresh score even when one already exists.
    if not force and application.hard_filter_score is not None:
        await _merge_pipeline_status(session, application_id, state="ok")
        await session.commit()
        logger.info(
            "scoring.hard_filter.skipped",
            extra={"application_id": str(application_id)},
        )
        return

    if force and application.hard_filter_score is not None:
        # Null the denormalised columns so the detail endpoint reflects
        # "scoring in progress" instead of the stale value. The audit
        # history in ``application_scores`` retains the prior attempt.
        await session.execute(
            sa.update(Application)
            .where(Application.id == application_id)
            .values(hard_filter_score=None, hard_filter_breakdown=None)
        )

    candidate = await session.get(Candidate, application.candidate_id)
    cv_doc = await session.get(CvDocument, application.cv_document_id)
    job = await session.get(Job, application.job_id)

    if candidate is None or cv_doc is None or job is None:
        await _merge_pipeline_status(
            session, application_id, state="failed", error="related_rows_missing"
        )
        await session.commit()
        logger.warning(
            "scoring.hard_filter.missing_deps",
            extra={"application_id": str(application_id)},
        )
        return

    if not candidate.parsed_profile:
        await _merge_pipeline_status(
            session, application_id, state="failed", error="parsed_profile_missing"
        )
        await session.commit()
        logger.warning(
            "scoring.hard_filter.no_parse",
            extra={"application_id": str(application_id)},
        )
        return

    if not cv_doc.extracted_text:
        await _merge_pipeline_status(
            session, application_id, state="failed", error="extracted_text_missing"
        )
        await session.commit()
        logger.warning(
            "scoring.hard_filter.no_text",
            extra={"application_id": str(application_id)},
        )
        return

    try:
        parsed_cv = ParsedCV.model_validate(candidate.parsed_profile)
    except Exception as exc:
        await _merge_pipeline_status(
            session,
            application_id,
            state="failed",
            error=f"parsed_profile_invalid: {exc}",
        )
        await session.commit()
        return

    # Mark pending so HR sees in-progress while Claude is working.
    # Commit early so the visibility lands even if the call is slow.
    await _merge_pipeline_status(session, application_id, state="pending")
    await session.commit()

    try:
        result = await score_hard_filters(
            job=job,
            parsed_cv=parsed_cv,
            cv_text=cv_doc.extracted_text,
            use_cache=not force,
        )
    except _TRANSIENT_ANTHROPIC as exc:
        # Leave pipeline_status at "pending"; Celery will retry.
        raise TransientHardFilterError("Anthropic transient error") from exc
    except HardFilterScoringError as exc:
        await _merge_pipeline_status(
            session,
            application_id,
            state="failed",
            error=str(exc),
        )
        await session.commit()
        await _publish_hard_filter_event(application, state=AppEventState.FAILED)
        logger.exception(
            "scoring.hard_filter.failed",
            extra={"application_id": str(application_id)},
        )
        return

    # Persist score + breakdown + pipeline_status in a single raw UPDATE
    # so we don't have to juggle ORM-pending state alongside the
    # JSONB-merge UPDATE. One server-side statement keeps the writes
    # atomic and avoids the synchronize_session expire dance.
    #
    # ``hard_filter_breakdown`` is passed as a plain dict so SQLAlchemy
    # binds it via the JSONB column's normal adapter -- explicitly
    # ``sa.cast(sa.literal(json.dumps(...)), JSONB)`` would also work but
    # bypasses the column's type machinery, leaving the in-memory ORM
    # row's attribute as ``None`` after the UPDATE (synchronize_session
    # only propagates direct scalar values).
    status_patch_jsonb = sa.cast(sa.literal(json.dumps({HARD_FILTER_STEP_KEY: "ok"})), JSONB)
    await session.execute(
        sa.update(Application)
        .where(Application.id == application_id)
        .values(
            hard_filter_score=result.total,
            hard_filter_breakdown=_serialise_breakdown(result),
            pipeline_status=Application.pipeline_status.op("-")(HARD_FILTER_ERROR_KEY).op("||")(
                status_patch_jsonb
            ),
        )
    )

    _write_audit_row(session, application_id=application_id, result=result)
    await session.commit()
    await _publish_hard_filter_event(application, state=AppEventState.OK)

    logger.info(
        "scoring.hard_filter.ok",
        extra={
            "application_id": str(application_id),
            "total": round(result.total, 2),
        },
    )


async def _run_with_session(application_id: uuid.UUID, *, force: bool) -> None:
    """Open a fresh AsyncSession; run the helper. Celery's bridge."""
    async with SessionFactory() as session:
        await score_hard_filter_for_application(application_id, session=session, force=force)


# --- Celery task wrapper ----------------------------------------------------


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.score_hard_filters",
    queue=SLOW_QUEUE,
    autoretry_for=(TransientHardFilterError,),
    retry_kwargs={
        "max_retries": HARD_FILTER_MAX_RETRIES,
        "countdown": HARD_FILTER_RETRY_BACKOFF_SECONDS,
    },
    retry_backoff=True,
    retry_backoff_max=HARD_FILTER_RETRY_BACKOFF_SECONDS * 4,
    retry_jitter=False,
)
def score_hard_filters_task(application_id: str, force: bool = False) -> None:
    """Score the application's CV against the JD via Claude Sonnet.

    ``force=True`` is the Step 4.4 rescore path: bypasses the
    idempotent-skip on existing scores AND the Claude Redis cache.
    """
    asyncio.run(_run_with_session(uuid.UUID(application_id), force=force))
