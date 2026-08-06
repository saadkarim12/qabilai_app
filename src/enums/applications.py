"""Application enumerations.

An :class:`Application` row represents one candidate's submission to one
job. It moves through pipeline *stages* (Step 1 vector screen → Step 2
hard filter → Step 3 WhatsApp invite → Step 4 scheduling) and carries an
overall *status* (active / rejected / etc.) that's orthogonal to stage.

For Step 3.8 we only need the entry-stage and the two lifecycle states
the public upload endpoint sets: ``VECTOR_SCREEN`` and ``ACTIVE``.
Subsequent phases extend these enums — keep the existing values stable
so historical rows continue to parse.
"""

from __future__ import annotations

from enum import StrEnum


class ApplicationStage(StrEnum):
    """Pipeline stage the application is currently in.

    The doc's transition matrix:

        vector_screen → hard_filter → whatsapp → interview → done

    Step 3.8 inserts at ``VECTOR_SCREEN``. Step 4.2 introduces the
    PATCH endpoints that walk the remaining four. ``DONE`` is the
    terminal active-pipeline state (an *accepted* offer flips ``status``
    to ACCEPTED but stage stays where it was).
    """

    VECTOR_SCREEN = "vector_screen"
    HARD_FILTER = "hard_filter"
    WHATSAPP = "whatsapp"
    INTERVIEW = "interview"
    DONE = "done"


class ApplicationStatus(StrEnum):
    """Lifecycle flag orthogonal to :class:`ApplicationStage`.

    * ``ACTIVE`` — in pipeline.
    * ``REJECTED`` — out of pipeline. Stage is preserved so HR can see
      *where* the rejection happened. Step 3.9's auto-rejection writes
      this; Step 4.2's manual override also writes it. HR can flip
      ``rejected → active`` (same stage) per doc.
    * ``ACCEPTED`` — terminal. An offer was made and accepted. Step 4.2
      treats this as a terminal status; no further transitions allowed.
    * ``ARCHIVED`` — pulled out of a job's pipeline back into the talent
      pool. Unlike a hard delete, the row (and its ``application_scores`` +
      WhatsApp transcript) is retained so the candidate's cross-job history
      survives. Stage is preserved so HR sees *where* the stint ended. Set
      only by the move-to-pool flow; excluded from active-pipeline listings
      and from the ``(candidate, job)`` live-uniqueness rule (so the
      candidate can be sourced back onto the same job for a fresh stint).
    """

    ACTIVE = "active"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    ARCHIVED = "archived"
