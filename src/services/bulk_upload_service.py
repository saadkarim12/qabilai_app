"""HR bulk-CV-upload orchestration.

Single public coroutine :func:`process_batch` runs one bulk-upload
request end-to-end:

1. **Pre-flight gate** — job must exist and be ``status='open'``;
   anything else raises :class:`BulkUploadJobNotOpenError` (422). The
   route layer's path / form validation already enforces the file-
   count bound, so the service trusts the submissions list it gets.
2. **Per-file pre-validation** (CPU-only, no I/O):

   - magic-byte check → :data:`BulkUploadRejectionReason.NOT_PDF`
   - sha256 dedup *within the batch* — second-and-later occurrences
     of the same bytes get :data:`BulkUploadRejectionReason.DUPLICATE_IN_BATCH`

   Files already flagged by the route as :data:`...TOO_LARGE` are
   passed through untouched.
3. **Parallel extraction fan-out** under an :class:`asyncio.Semaphore`
   of 10:

   - extract PDF text (Redis cache keyed on sha256 in
     :func:`src.utils.pdf.extract_text`) plus page-1 structural signals
     (:func:`src.utils.pdf.extract_page1_signals`) — largest-font name
     candidates and the de-glued page text;
   - extract contact details
     (:func:`src.ai.contact_extractor.extract_contact_details`), preferring
     the size-ranked name and the de-glued email, and only falling back to
     Claude Haiku (7-day Redis cache) when the deterministic pass is
     inconclusive.

   The Azure upload is *not* done here — see step 4.
4. **Sequential DB writes** — find-or-create candidate, then upload the
   PDF to Azure Blob under the *real* candidate id
   (``cvs/{candidate_id}/{sha256}.pdf`` — exactly the path the Step 3.9
   pipeline reconstructs to fetch the CV; uploading under a batch-scoped
   id here would leave the pipeline unable to find the blob), find-or-
   create cv_document (idempotent by sha256), find-or-create application.
   Already-applied candidates skip the new application and land in
   ``already_applied``. Phone normalization happens here so the
   :class:`Candidate` row always carries E.164.
5. **Enqueue** the Step 3.9 Celery CV pipeline once per newly-created
   application — never for already-applied rows (whose pipeline already
   ran or is in-flight from the prior submission). This dispatch is the
   route's job, done *after* it commits (see below): enqueuing inside
   the transaction races the commit and the worker can hit
   ``dispatch.app_missing`` on a not-yet-visible row.
6. **Audit trail** — one ``audit_logs`` row per created application
   (``entity_type='application'``) plus one batch-summary row
   (``entity_type='job'``). Both carry the same ``batch_id`` in
   ``after_state`` so the two views are joinable.

The whole flow runs inside the request's single :class:`AsyncSession`
transaction. The route layer commits, then dispatches the CV pipeline
for each created application (step 5).

CV bytes, extracted text, and Claude responses are never logged —
candidate-PII / confidential. Log records carry batch_id, counts, and
filename only.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.contact_extractor import ContactDetails, extract_contact_details
from src.db.models.application import Application
from src.db.models.audit_log import AuditLog
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.job import Job
from src.db.models.user import User
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.audit import AuditAction, AuditEntityType
from src.enums.jobs import JobStatus
from src.exceptions import BulkUploadJobNotOpenError, JobNotFoundError
from src.integrations.azure_blob import (
    PDF_MAGIC_BYTES,
    UploadedBlob,
    upload_pdf,
)
from src.schemas.bulk_upload import (
    BULK_UPLOAD_FILENAME_MAX_LENGTH,
    BulkUploadAlreadyAppliedItem,
    BulkUploadCreatedApplication,
    BulkUploadRejectedItem,
    BulkUploadRejectionReason,
    BulkUploadResponse,
)
from src.utils.pdf import extract_page1_signals, extract_text
from src.utils.phone import InvalidPhoneNumberError, normalize_phone

logger = logging.getLogger(__name__)

# Bound on concurrent network calls per batch. Each accepted file
# triggers an Azure upload, a Redis-cached pypdf extraction (rarely a
# fresh CPU job), and a Claude Haiku call; the doc caps fan-out at 10
# so a 50-file batch never has more than ten flights in the air.
BULK_UPLOAD_CONCURRENCY: Final[int] = 10


@dataclass(frozen=True, slots=True)
class FileSubmission:
    """One file's bytes + filename as handed to the service by the route.

    ``prevalidation_error`` is set when the route already knows the file
    can't be processed (e.g. it exceeded the size cap and was never
    fully read). When set, ``pdf_bytes`` is ``None`` and the service
    passes the rejection straight through to the response without doing
    any further work.
    """

    filename: str
    pdf_bytes: bytes | None
    prevalidation_error: BulkUploadRejectionReason | None = None


@dataclass(frozen=True, slots=True)
class _AcceptedFile:
    """Intermediate record for a file that passed pre-validation.

    Populated by :func:`_extract_for_file` with the extracted text and
    contact details so the DB-write phase can find-or-create the
    candidate. The PDF bytes are carried through so the DB phase can do
    the Azure upload itself, keyed by the real candidate id (which isn't
    known until the contact details extracted here resolve a candidate).
    """

    filename: str
    sha256: str
    pdf_bytes: bytes
    extracted_text: str
    contact: ContactDetails


def _sanitize_filename(raw: str | None) -> str:
    """Strip path components and cap length; never trust client filenames.

    Bulk-upload responses surface filenames back to HR and audit_logs
    persists them, so a malicious uploader can't dump a path-shaped
    name into either by design — but defensive trimming costs nothing.
    """
    if not raw:
        return "unnamed.pdf"
    # rsplit handles both forward- and back-slashes regardless of OS.
    cleaned = raw.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    if not cleaned:
        return "unnamed.pdf"
    if len(cleaned) > BULK_UPLOAD_FILENAME_MAX_LENGTH:
        cleaned = cleaned[:BULK_UPLOAD_FILENAME_MAX_LENGTH]
    return cleaned


async def _assert_job_open(session: AsyncSession, job_id: uuid.UUID) -> Job:
    """Load the job and refuse if it isn't ``status='open'``."""
    job = await session.get(Job, job_id)
    if job is None:
        raise JobNotFoundError(f"Job {job_id} not found")
    if job.status is not JobStatus.OPEN:
        raise BulkUploadJobNotOpenError(
            f"Job {job_id} is not open for applications (status={job.status.value})",
            details={"job_id": str(job_id), "status": job.status.value},
        )
    return job


def _pre_validate(
    submissions: Iterable[FileSubmission],
) -> tuple[
    list[_PendingFile],
    list[BulkUploadRejectedItem],
]:
    """Apply CPU-only validation: magic bytes + batch dedup.

    Returns the still-accepted files plus any rejection records
    accumulated so far (TOO_LARGE pass-throughs, NOT_PDF magic-byte
    failures, and DUPLICATE_IN_BATCH sha256 collisions).
    """
    accepted: list[_PendingFile] = []
    rejected: list[BulkUploadRejectedItem] = []
    seen_sha256s: set[str] = set()

    for submission in submissions:
        filename = _sanitize_filename(submission.filename)

        if submission.prevalidation_error is not None:
            rejected.append(
                BulkUploadRejectedItem(filename=filename, reason=submission.prevalidation_error)
            )
            continue

        if submission.pdf_bytes is None:
            # Defensive: a submission without bytes and without an error
            # flag shouldn't happen, but treat it as TOO_LARGE so the
            # response still describes the file.
            rejected.append(
                BulkUploadRejectedItem(
                    filename=filename,
                    reason=BulkUploadRejectionReason.TOO_LARGE,
                )
            )
            continue

        if not submission.pdf_bytes.startswith(PDF_MAGIC_BYTES):
            rejected.append(
                BulkUploadRejectedItem(
                    filename=filename,
                    reason=BulkUploadRejectionReason.NOT_PDF,
                )
            )
            continue

        digest = hashlib.sha256(submission.pdf_bytes).hexdigest()
        if digest in seen_sha256s:
            rejected.append(
                BulkUploadRejectedItem(
                    filename=filename,
                    reason=BulkUploadRejectionReason.DUPLICATE_IN_BATCH,
                )
            )
            continue
        seen_sha256s.add(digest)

        accepted.append(
            _PendingFile(filename=filename, pdf_bytes=submission.pdf_bytes, sha256=digest)
        )

    return accepted, rejected


@dataclass(frozen=True, slots=True)
class _PendingFile:
    """A pre-validated file awaiting the parallel I/O phase."""

    filename: str
    pdf_bytes: bytes
    sha256: str


async def _extract_for_file(
    pending: _PendingFile,
    *,
    semaphore: asyncio.Semaphore,
) -> _AcceptedFile:
    """Extract PDF text + contact details for one file under the semaphore.

    The Azure upload is deliberately *not* done here. The blob path is
    keyed by the real candidate id (``cvs/{candidate_id}/{sha256}.pdf``),
    and the Step 3.9 pipeline reconstructs that exact path to fetch the
    CV. The candidate id is only known once the contact details extracted
    here let :func:`process_batch` find-or-create the candidate, so the
    upload happens there, in the sequential DB phase.

    Returns an :class:`_AcceptedFile` carrying the text + contact so the
    DB-write phase needs no further Claude / pypdf work.
    """
    async with semaphore:
        # extract_text is sha256-cached in Redis; a fresh call is at
        # most one pypdf extraction per unique CV.
        text = await extract_text(pending.pdf_bytes)
        # Page-1 structural signals (one pypdf pass, first page only, uncached;
        # empty on any pypdf failure): the largest-font chunks so the
        # deterministic pass picks the name over a job-title banner, and the
        # de-glued page text so a label-glued email is read cleanly.
        signals = await extract_page1_signals(pending.pdf_bytes)
        # The Claude call is sha256-cached too; a re-upload of the same CV
        # text hits the cache and never touches Anthropic.
        contact = await extract_contact_details(
            text,
            name_candidates=signals.name_candidates,
            contact_block_text=signals.deglued_text,
        )

    return _AcceptedFile(
        filename=pending.filename,
        sha256=pending.sha256,
        pdf_bytes=pending.pdf_bytes,
        extracted_text=text,
        contact=contact,
    )


async def _find_or_create_candidate_locked(
    session: AsyncSession,
    *,
    email: str | None,
    phone_e164: str | None,
    full_name: str,
) -> Candidate:
    """Find-or-create keyed on whichever contact field(s) are present.

    Unlike the public-apply intake (which always has both email *and*
    phone from the consent form), the bulk uploader may resolve only one
    of the two from a CV. Identity matches on the present fields and
    requires the absent one to be NULL, mirroring the three partial
    UNIQUE indexes from migration ``0013``:

      - both present : ``LOWER(email) = … AND phone_e164 = …``
      - email only   : ``LOWER(email) = … AND phone_e164 IS NULL``
      - phone only   : ``phone_e164 = … AND email IS NULL``

    At least one of ``email``/``phone_e164`` is always non-None here — the
    caller rejects a CV with neither as ``parse_failed_no_contact``.
    """
    conditions = [
        sa.func.lower(Candidate.email) == email.lower()
        if email is not None
        else Candidate.email.is_(None),
        Candidate.phone_e164 == phone_e164
        if phone_e164 is not None
        else Candidate.phone_e164.is_(None),
    ]
    existing = (await session.execute(sa.select(Candidate).where(*conditions))).scalar_one_or_none()
    if existing is not None:
        return existing
    candidate = Candidate(email=email, phone_e164=phone_e164, full_name=full_name)
    session.add(candidate)
    await session.flush()
    return candidate


async def _find_or_create_cv_document(
    session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    blob: UploadedBlob,
) -> CvDocument:
    """Reuse an existing CV row (same candidate, same sha256) or insert.

    Mirrors the intake-service flow: when this candidate has a CV with
    a different sha256 already marked current, we demote it before
    inserting the new one to satisfy the partial UNIQUE.
    """
    stmt = sa.select(CvDocument).where(
        CvDocument.candidate_id == candidate_id,
        CvDocument.blob_sha256 == blob.sha256,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    await session.execute(
        sa.update(CvDocument)
        .where(CvDocument.candidate_id == candidate_id, CvDocument.is_current.is_(True))
        .values(is_current=False)
    )

    cv_doc = CvDocument(
        candidate_id=candidate_id,
        blob_url=blob.url,
        blob_sha256=blob.sha256,
        is_current=True,
    )
    session.add(cv_doc)
    await session.flush()
    return cv_doc


async def _find_existing_application(
    session: AsyncSession, *, candidate_id: uuid.UUID, job_id: uuid.UUID
) -> Application | None:
    # Exclude archived stints (move-to-pool) so a candidate uploaded again
    # after leaving the pipeline gets a fresh application instead of the dead
    # one — mirrors the partial unique index on non-archived rows.
    stmt = sa.select(Application).where(
        Application.candidate_id == candidate_id,
        Application.job_id == job_id,
        Application.status != ApplicationStatus.ARCHIVED,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _build_hr_consent_context(actor: User, ip: str | None) -> dict[str, str]:
    """Audit-style snapshot for HR-driven uploads.

    Unlike the public-apply route, the candidate didn't tick a consent
    box — the HR admin is acting on their behalf, having (per the
    architecture doc) obtained consent through a separate process. We
    still store who-when-where so the audit trail is complete.
    """
    return {
        "actor_user_id": str(actor.id),
        "actor_email": actor.email,
        "ip": ip or "unknown",
        "accepted_at": datetime.now(UTC).isoformat(),
        "source": "hr_bulk_upload",
    }


def _enqueue_cv_pipeline(application_id: uuid.UUID) -> None:
    """Dispatch the Step 3.9 CV pipeline for one created application.

    Called by the route *after* it commits the bulk-upload transaction
    (never from inside :func:`process_batch`), so the worker only ever
    sees committed, visible application rows — avoiding the
    ``dispatch.app_missing`` race.

    Lives in a top-level function so tests can monkey-patch it without
    touching Celery's broker config. Imports inside the function to
    keep Celery initialisation out of module import.
    """
    from src.workers.tasks.cv_pipeline import dispatch_cv_processing_task

    dispatch_cv_processing_task.delay(str(application_id))


async def process_batch(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    actor: User,
    ip: str | None,
    submissions: list[FileSubmission],
) -> BulkUploadResponse:
    """Orchestrate one bulk-upload request end-to-end.

    See module docstring for the step-by-step flow. The caller (route
    handler) owns the transaction — this coroutine flushes its writes
    so they're queryable within the request, but does not commit, and
    does not dispatch the CV pipeline. The route commits first, then
    enqueues a pipeline for each ``applications`` entry in the returned
    response (avoiding the dispatch-before-commit race).
    """
    batch_id = uuid.uuid4()
    job = await _assert_job_open(session, job_id)

    pending, rejected = _pre_validate(submissions)

    # Parallel extraction phase: pypdf text + Claude contact details per
    # file, bounded by the semaphore. No Azure upload here — the blob
    # path is keyed by the real candidate id, which is only resolved in
    # the sequential DB phase below.
    semaphore = asyncio.Semaphore(BULK_UPLOAD_CONCURRENCY)
    pipeline_results = await asyncio.gather(
        *[_extract_for_file(p, semaphore=semaphore) for p in pending]
    )

    # Sequential DB phase. Each accepted file resolves to one of:
    # - newly-created application (added to ``created``)
    # - already-applied candidate on this job (added to ``already_applied``)
    # - parse_failed_no_contact rejection (added to ``rejected``)
    created: list[BulkUploadCreatedApplication] = []
    already_applied: list[BulkUploadAlreadyAppliedItem] = []

    for result in pipeline_results:
        contact = result.contact

        # Gate: a CV must yield a name plus *at least one* contact field
        # (email or phone) to enter the system. A résumé with only one of
        # the two still gets in — the candidate identity degrades to that
        # single field (see ``_find_or_create_candidate_locked``).
        phone_e164: str | None = None
        if contact.phone is not None:
            try:
                phone_e164 = normalize_phone(contact.phone)
            except InvalidPhoneNumberError:
                # The sanitizer inside contact_extractor already drops
                # un-normalizable phones to None, so this is a belt-and-
                # braces guard rather than an expected branch.
                phone_e164 = None

        if contact.full_name is None or (contact.email is None and phone_e164 is None):
            rejected.append(
                BulkUploadRejectedItem(
                    filename=result.filename,
                    reason=BulkUploadRejectionReason.PARSE_FAILED_NO_CONTACT,
                )
            )
            continue

        candidate = await _find_or_create_candidate_locked(
            session,
            email=contact.email,
            phone_e164=phone_e164,
            full_name=contact.full_name,
        )

        existing_application = await _find_existing_application(
            session, candidate_id=candidate.id, job_id=job_id
        )
        if existing_application is not None:
            already_applied.append(
                BulkUploadAlreadyAppliedItem(
                    application_id=existing_application.id,
                    candidate_id=candidate.id,
                    filename=result.filename,
                )
            )
            continue

        # Upload now that the real candidate id is known, so the blob
        # lands at cvs/{candidate_id}/{sha256}.pdf — exactly where the
        # Step 3.9 pipeline reconstructs and fetches it. upload_pdf is
        # idempotent (overwrite=True), so re-uploading an identical CV
        # just rewrites the same path.
        blob = await upload_pdf(result.pdf_bytes, candidate_id=candidate.id)
        cv_document = await _find_or_create_cv_document(
            session, candidate_id=candidate.id, blob=blob
        )

        consent_context = _build_hr_consent_context(actor, ip)
        application = Application(
            candidate_id=candidate.id,
            job_id=job.id,
            cv_document_id=cv_document.id,
            stage=ApplicationStage.VECTOR_SCREEN,
            status=ApplicationStatus.ACTIVE,
            consent_context=consent_context,
            consented_at=datetime.now(UTC),
        )
        session.add(application)
        await session.flush()

        session.add(
            AuditLog(
                user_id=actor.id,
                entity_type=AuditEntityType.APPLICATION.value,
                entity_id=application.id,
                action=AuditAction.BULK_UPLOAD.value,
                before_state=None,
                after_state={
                    "batch_id": str(batch_id),
                    "filename": result.filename,
                    "source": "hr_bulk_upload",
                },
                ip_address=ip,
            )
        )
        await session.flush()

        # NB: the CV pipeline is NOT dispatched here. Enqueuing mid-
        # transaction races the route's commit — the worker can pick up
        # the task and query for an application row that isn't visible
        # yet, hitting ``cv_pipeline.dispatch.app_missing`` and silently
        # dropping the CV. The route enqueues each created application
        # *after* it commits (see ``_enqueue_cv_pipeline``).
        created.append(
            BulkUploadCreatedApplication(
                application_id=application.id,
                candidate_id=candidate.id,
                filename=result.filename,
            )
        )

    accepted_count = len(created) + len(already_applied)
    rejected_count = len(rejected)

    # Batch summary audit row keyed on the job so HR can pull the
    # full picture of one upload action by (entity_type='job', batch_id).
    session.add(
        AuditLog(
            user_id=actor.id,
            entity_type=AuditEntityType.JOB.value,
            entity_id=job.id,
            action=AuditAction.BULK_UPLOAD.value,
            before_state=None,
            after_state={
                "batch_id": str(batch_id),
                "submitted_count": len(submissions),
                "accepted_count": accepted_count,
                "rejected_count": rejected_count,
                "applications_created": [str(item.application_id) for item in created],
                "already_applied": [str(item.application_id) for item in already_applied],
                "rejection_counts": _count_rejections(rejected),
            },
            ip_address=ip,
        )
    )
    await session.flush()

    logger.info(
        "bulk_upload.ok",
        extra={
            "batch_id": str(batch_id),
            "job_id": str(job_id),
            "actor_user_id": str(actor.id),
            "submitted_count": len(submissions),
            "accepted_count": accepted_count,
            "rejected_count": rejected_count,
        },
    )

    return BulkUploadResponse(
        batch_id=batch_id,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        applications=created,
        already_applied=already_applied,
        rejected=rejected,
    )


def _count_rejections(rejected: list[BulkUploadRejectedItem]) -> dict[str, int]:
    """Aggregate rejection reasons for the batch summary audit row."""
    counts: dict[str, int] = {}
    for item in rejected:
        counts[item.reason.value] = counts.get(item.reason.value, 0) + 1
    return counts
