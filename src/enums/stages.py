"""Application stage- and status-transition matrices (Step 4.2).

Pure data — the service layer consults these to decide whether a PATCH
is allowed; tests can iterate them to assert the full surface. Keeping
the matrices here (rather than inlined in the service) means the doc's
"allowed transitions" list has exactly one source of truth in the code.

Doc reference (Step 4.2):

    vector_screen → hard_filter      (manual or auto)
    hard_filter   → whatsapp         (manual)
    whatsapp      → interview        (manual)
    interview     → done             (manual)

    Any → rejected / accepted        (manual)
    rejected → active                (HR override, same stage)
    accepted is terminal             (per session decision 2026-05-24)
"""

from __future__ import annotations

from typing import Final

from src.enums.applications import ApplicationStage, ApplicationStatus

# --- Stage transitions ------------------------------------------------------
#
# A move from key → any value in the frozenset is allowed. A stage move
# is only permitted while ``status == ACTIVE``: rejecting / accepting
# freezes the stage in place so HR can see exactly where the candidate
# was when the decision was made.

ALLOWED_STAGE_TRANSITIONS: Final[dict[ApplicationStage, frozenset[ApplicationStage]]] = {
    ApplicationStage.VECTOR_SCREEN: frozenset({ApplicationStage.HARD_FILTER}),
    ApplicationStage.HARD_FILTER: frozenset({ApplicationStage.WHATSAPP}),
    ApplicationStage.WHATSAPP: frozenset({ApplicationStage.INTERVIEW}),
    ApplicationStage.INTERVIEW: frozenset({ApplicationStage.DONE}),
    ApplicationStage.DONE: frozenset(),
}

# --- Candidate contact-edit window ------------------------------------------
#
# HR may edit the candidate's name / email / phone only while the application
# sits in one of these stages. From ``whatsapp`` onward the automated screening
# (and later the Calendly interview invite) reaches the candidate using those
# details, so they freeze: editing them mid-conversation would silently
# retarget or orphan a live WhatsApp/interview thread.

CONTACT_EDITABLE_STAGES: Final[frozenset[ApplicationStage]] = frozenset(
    {ApplicationStage.VECTOR_SCREEN, ApplicationStage.HARD_FILTER}
)

# --- Status transitions -----------------------------------------------------

ALLOWED_STATUS_TRANSITIONS: Final[dict[ApplicationStatus, frozenset[ApplicationStatus]]] = {
    ApplicationStatus.ACTIVE: frozenset({ApplicationStatus.REJECTED, ApplicationStatus.ACCEPTED}),
    # Same-stage override: HR brings a rejected candidate back into the
    # pipeline at the stage they were rejected from.
    ApplicationStatus.REJECTED: frozenset({ApplicationStatus.ACTIVE}),
    # Terminal: accepted offers don't move. Operator intervention via
    # direct DB only.
    ApplicationStatus.ACCEPTED: frozenset(),
    # ``ARCHIVED`` is intentionally absent (no key, no target): it's a
    # history-preserving terminal state reachable *only* via move-to-pool,
    # never the status PATCH. Re-entry is by sourcing a fresh application
    # from the pool, not by un-archiving this row.
}
