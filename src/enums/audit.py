"""Audit-log enumerations.

The ``audit_logs`` table is the system-wide chronological record of HR
actions and noteworthy state transitions. Step 4.1 ships the table and
the read endpoint; Step 4.2 wires the first writes (stage / status
transitions). The enums here describe the action vocabulary we expect
to grow into.

Both columns are ``varchar`` in the DB (per architecture-doc schema), so
adding a value later is a code-only change — no migration needed.
"""

from __future__ import annotations

from enum import StrEnum


class AuditEntityType(StrEnum):
    """``audit_logs.entity_type`` values.

    Naming mirrors the corresponding table for round-trippability between
    audit row and source row: ``application`` → ``applications.id``.
    """

    APPLICATION = "application"
    JOB = "job"
    CANDIDATE = "candidate"
    USER = "user"


class AuditAction(StrEnum):
    """``audit_logs.action`` vocabulary.

    Step 4.1 introduces the symbols; the first writes land in Step 4.2.
    The doc lists ``'stage_changed' / 'manually_accepted' / etc.`` as
    examples, so we add what we know will be needed and leave the column
    free-form to accommodate future actions without enum churn.
    """

    STAGE_CHANGED = "stage_changed"
    STATUS_CHANGED = "status_changed"
    MANUALLY_ACCEPTED = "manually_accepted"
    MANUALLY_REJECTED = "manually_rejected"
    RESCORE_REQUESTED = "rescore_requested"
    # Emitted by the HR bulk-upload endpoint. Both the per-application
    # entries (entity_type='application') and the batch-summary entry
    # (entity_type='job') share this action — they're linkable via
    # ``after_state.batch_id``.
    BULK_UPLOAD = "bulk_upload"
    # System action (user_id NULL): the candidate replied on WhatsApp that
    # they're no longer interested, so the screening flow auto-rejected the
    # application. Distinct from ``manually_rejected`` (an HR decision).
    WHATSAPP_DECLINED = "whatsapp_declined"
    # HR added a candidate to the talent pool (entity_type='candidate').
    TALENT_POOL_ADDED = "talent_pool_added"
    # HR sourced a pooled candidate onto a job, creating a fresh application
    # (entity_type='application'); the new app carries ``sourced_from_talent_pool``.
    TALENT_POOL_SOURCED = "talent_pool_sourced"
    # HR moved a candidate out of a job's pipeline back into the talent pool
    # (entity_type='application'); the application row is hard-deleted (this
    # entry survives by design — ``entity_id`` is not FK'd) and the candidate
    # is (re-)added to the pool.
    APPLICATION_MOVED_TO_POOL = "application_moved_to_pool"
    # HR edited the candidate's contact details (name / email / phone) from an
    # application before WhatsApp screening began (entity_type='application').
    # ``before_state`` / ``after_state`` hold the changed contact fields.
    CANDIDATE_DETAILS_UPDATED = "candidate_details_updated"
    # HR recorded/updated the post-interview evaluation (mark + optional comment)
    # for an application at the ``interview`` stage (entity_type='application').
    # ``after_state`` holds the new mark + whether a comment was set; the
    # free-text comment itself is not copied into the audit trail.
    INTERVIEW_SCORED = "interview_scored"
