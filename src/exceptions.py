"""Domain exception hierarchy.

All application-defined errors inherit from :class:`KabilError` so the
global exception handler can format them consistently. Subclasses set
sensible class-level defaults for HTTP status and error code; instances
can override per-raise.
"""

from __future__ import annotations

from typing import Any


class KabilError(Exception):
    """Base class for all application-defined errors."""

    http_status: int = 500
    error_code: str = "internal_server_error"

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if http_status is not None:
            self.http_status = http_status
        if error_code is not None:
            self.error_code = error_code
        self.details = details


class InvalidTokenError(KabilError):
    """Raised by JWT decode for expired, tampered, or malformed tokens."""

    http_status = 401
    error_code = "invalid_token"


class InvalidCredentialsError(KabilError):
    """Raised by ``auth_service.login`` for wrong email or password.

    Same error code for both so the response can't be used to enumerate which
    field is wrong.
    """

    http_status = 401
    error_code = "invalid_credentials"


class JobNotFoundError(KabilError):
    """Raised when a ``GET``/``PATCH`` targets a job id that doesn't exist."""

    http_status = 404
    error_code = "job_not_found"


class IllegalStatusTransitionError(KabilError):
    """Raised when an HR user attempts an unsupported job-status transition.

    The lifecycle matrix (draft→open, open→closed, closed→open) is encoded
    in ``job_service.update_status``; any other edge yields this error.
    """

    http_status = 409
    error_code = "illegal_status_transition"


# --- AI job-description builder ---------------------------------------------


class AiServiceUnavailableError(KabilError):
    """Raised when an AI-backed endpoint is hit but the provider isn't configured.

    Distinct from a transient provider error: this is a deployment/config gap
    (no ``ANTHROPIC_API_KEY``), so 503 signals "try later / not my request".
    """

    http_status = 503
    error_code = "ai_service_unavailable"


class JobDescriptionGenerationError(KabilError):
    """Raised when the AI JD builder can't produce a usable description.

    Covers a transient provider failure or an unusably short/empty draft. 502
    (bad gateway) marks it as an upstream-AI problem the client can retry, not a
    fault in the request itself.
    """

    http_status = 502
    error_code = "job_description_generation_failed"


# --- Public CV upload (Step 3.8) --------------------------------------------


class SlugNotFoundError(KabilError):
    """Raised when the public-apply slug is unknown or inactive.

    The HTTP response code is 410 Gone — the link "existed" in the
    sense that it was issued, but it's no longer valid. We deliberately
    use the same error for "never existed" and "rotated to inactive" so
    a third party can't enumerate live slugs by status code.
    """

    http_status = 410
    error_code = "slug_inactive_or_unknown"


class ConsentNotGivenError(KabilError):
    """Raised when the consent flag on a public apply submission is false."""

    http_status = 400
    error_code = "consent_required"


class InvalidEmailError(KabilError):
    """Raised when the email on a public apply submission fails basic-format validation."""

    http_status = 400
    error_code = "invalid_email"


class PdfInvalidError(KabilError):
    """Raised when the uploaded file isn't a PDF (magic-byte check failed)."""

    http_status = 400
    error_code = "pdf_invalid"


class PdfTooLargeError(KabilError):
    """Raised when the uploaded PDF exceeds the configured size cap."""

    http_status = 413
    error_code = "pdf_too_large"


# --- Applications (Step 4.1) -----------------------------------------------


class ApplicationNotFoundError(KabilError):
    """Raised when a ``GET``/``PATCH`` targets an application id that doesn't exist."""

    http_status = 404
    error_code = "application_not_found"


# --- Talent pool ------------------------------------------------------------


class CandidateNotFoundError(KabilError):
    """Raised when a talent-pool action targets a candidate id that doesn't exist."""

    http_status = 404
    error_code = "candidate_not_found"


class CandidateNotInTalentPoolError(KabilError):
    """Raised when sourcing a candidate who has no active talent-pool entry."""

    http_status = 422
    error_code = "candidate_not_in_talent_pool"


class CandidateHasNoCvError(KabilError):
    """Raised when sourcing a candidate who has no current CV on file.

    Sourcing creates a fresh application pointing at the candidate's current
    CV; without one there's nothing to score.
    """

    http_status = 422
    error_code = "candidate_has_no_cv"


class JobNotOpenForSourcingError(KabilError):
    """Raised when sourcing a candidate onto a job whose status isn't ``open``."""

    http_status = 422
    error_code = "job_not_open_for_sourcing"


class CvParseNoContactError(KabilError):
    """Raised when a directly-uploaded talent-pool CV yields no usable identity.

    A CV must produce a name plus at least one contact field (email or phone)
    to create a candidate. Unlike bulk upload — which fails soft per file — a
    single direct upload has nothing else to report, so it raises.
    """

    http_status = 422
    error_code = "cv_parse_no_contact"


class SearchQueryRequiredError(KabilError):
    """Raised when a pool search supplies neither a text query nor a job_id."""

    http_status = 422
    error_code = "search_query_required"


class JobNotEmbeddedError(KabilError):
    """Raised when searching the pool by a job whose JD isn't embedded yet.

    A job's ``jd_embedding`` is populated asynchronously when it's opened. Until
    that lands there's no vector to rank pooled candidates against.
    """

    http_status = 409
    error_code = "job_not_embedded"


class WhatsAppConversationNotFoundError(KabilError):
    """Raised when an application has no WhatsApp screening conversation yet.

    The application exists but was never moved into the ``whatsapp`` stage (or
    the invite hasn't run), so there's no transcript to return.
    """

    http_status = 404
    error_code = "whatsapp_conversation_not_found"


# --- Application stage / status transitions (Step 4.2) ----------------------


class IllegalStageTransitionError(KabilError):
    """Raised on an HR-requested application stage move outside the allow-list.

    422 (not 409) per architecture doc Step 4.2: 'Invalid transition
    returns 422 with reason'. Reason details live on ``.details``.
    """

    http_status = 422
    error_code = "illegal_stage_transition"


class IllegalApplicationStatusTransitionError(KabilError):
    """Raised on an HR-requested application status move outside the allow-list.

    Same 422 semantics as the stage variant. Separate class so the
    audit log / response can distinguish stage vs status rejections
    without inspecting the ``details`` blob.
    """

    http_status = 422
    error_code = "illegal_application_status_transition"


# --- Bulk CV upload ---------------------------------------------------------


class BulkUploadJobNotOpenError(KabilError):
    """Raised when an HR bulk upload targets a job whose status isn't ``open``.

    HR can stage CVs against a draft job before publishing or hold off
    once a role is filled — this endpoint refuses both. 422 instead of
    409 mirrors the application-stage transition errors: the request is
    well-formed, but its target's lifecycle state forbids the action.
    """

    http_status = 422
    error_code = "job_not_open_for_applications"


# --- Candidate contact edit -------------------------------------------------


class CandidateContactLockedError(KabilError):
    """Raised when HR edits candidate contact details past the editable window.

    Name / email / phone are editable only while the application is in the
    ``vector_screen`` or ``hard_filter`` stage. From ``whatsapp`` onward the
    automated screening (and later the interview invite) communicates with the
    candidate using these details, so they're frozen — changing them mid-flow
    would silently retarget or orphan a live conversation. 422 mirrors the
    stage/status transition errors: well-formed request, forbidden by the
    target's lifecycle state. ``details`` carries the current ``stage``.
    """

    http_status = 422
    error_code = "candidate_contact_locked"


class InterviewFeedbackStageError(KabilError):
    """Raised when HR submits interview feedback outside the ``interview`` stage.

    The mark + comment are a post-interview evaluation, so they're accepted only
    while the application sits at the ``interview`` stage. 422 mirrors the
    stage/status transition errors: well-formed request, forbidden by the
    target's lifecycle state. ``details`` carries the current ``stage``.
    """

    http_status = 422
    error_code = "interview_feedback_wrong_stage"


class CandidateIdentityConflictError(KabilError):
    """Raised when an edited (email, phone) would collide with another candidate.

    Candidate identity is the partial-unique ``(LOWER(email), phone_e164)`` pair
    (see migration 0013). Editing one candidate's contact details to a pair that
    already belongs to a *different* candidate would violate that uniqueness, so
    we refuse with 409 rather than let the commit fail.
    """

    http_status = 409
    error_code = "candidate_identity_conflict"
