"""Pydantic request / response shapes for the HR bulk-CV-upload endpoint.

The endpoint accepts up to :data:`BULK_UPLOAD_MAX_FILES` PDFs in one
multipart request and returns a single envelope describing what
happened to each file: which created brand-new applications, which
matched a candidate who had already applied to this job, and which
were rejected (and why).

Rejection reasons are an :class:`enum.StrEnum` so the front-end can
switch on the literal string. Adding a new reason here also requires a
companion change in :mod:`src.services.bulk_upload_service` so the
service actually emits it.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

# Per-request file count bounds. The upper bound is the architecture
# limit (one HR upload action shouldn't be a stress-test); the lower
# bound rejects empty bodies that would otherwise produce a 0/0 response.
BULK_UPLOAD_MIN_FILES: Final[int] = 1
BULK_UPLOAD_MAX_FILES: Final[int] = 50

# Sanitised filename surfaced in responses + audit rows. Path components
# are stripped at the route layer; this cap keeps absurdly long names
# from bloating the response or audit_logs.after_state JSONB.
BULK_UPLOAD_FILENAME_MAX_LENGTH: Final[int] = 255


class BulkUploadRejectionReason(StrEnum):
    """Why a single file inside a bulk upload was rejected.

    The set is intentionally small: HR needs to know *why* without
    learning every internal failure mode. ``parse_failed_no_contact``
    is the catch-all for "Claude couldn't extract enough to create a
    Candidate row" — empty CV, no contact info, or contact details we
    can't normalize.
    """

    NOT_PDF = "not_pdf"
    TOO_LARGE = "too_large"
    DUPLICATE_IN_BATCH = "duplicate_in_batch"
    PARSE_FAILED_NO_CONTACT = "parse_failed_no_contact"


class BulkUploadRejectedItem(BaseModel):
    """One per-file rejection record."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=BULK_UPLOAD_FILENAME_MAX_LENGTH)
    reason: BulkUploadRejectionReason


class BulkUploadCreatedApplication(BaseModel):
    """One per-file record for a freshly-created application."""

    model_config = ConfigDict(extra="forbid")

    application_id: uuid.UUID
    candidate_id: uuid.UUID
    filename: str = Field(min_length=1, max_length=BULK_UPLOAD_FILENAME_MAX_LENGTH)


class BulkUploadAlreadyAppliedItem(BaseModel):
    """One per-file record for a candidate who already had an application on this job.

    No new application is created; the existing ``application_id`` is
    surfaced so HR can navigate to the prior submission directly from
    the response.
    """

    model_config = ConfigDict(extra="forbid")

    application_id: uuid.UUID
    candidate_id: uuid.UUID
    filename: str = Field(min_length=1, max_length=BULK_UPLOAD_FILENAME_MAX_LENGTH)


class BulkUploadResponse(BaseModel):
    """Envelope returned from ``POST /jobs/{job_id}/applications/bulk-upload``.

    ``accepted_count`` counts files that landed somewhere in the
    system — both newly created applications and those whose candidate
    already had an application on this job. ``rejected_count`` counts
    files that failed validation or contact extraction; the
    ``rejected`` list explains each one.
    """

    model_config = ConfigDict(extra="forbid")

    batch_id: uuid.UUID
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    applications: list[BulkUploadCreatedApplication]
    already_applied: list[BulkUploadAlreadyAppliedItem]
    rejected: list[BulkUploadRejectedItem]
