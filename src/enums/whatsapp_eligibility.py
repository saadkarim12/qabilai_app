"""WhatsApp eligibility-extraction constants (Step 5.5).

The fixed screening questions are answered in free text; the eligibility
extractor (:mod:`src.services.whatsapp_eligibility_extractor`) turns each answer
into a small normalized value the frontend renders on the score card
(``15,800 AED`` / ``3 Weeks`` / ``Yes`` / ``Full Time`` …).

:class:`EligibilityKey` is the closed set of fixed-question ``source_field``
keys we extract. It is deliberately a **subset** of
:data:`src.enums.fixed_screening_questions.FIXED_QUESTION_KEYS` — the
``commitment`` (motivation) question is excluded because its verdict is derived
deterministically from the conversation state (interest + completion), not
parsed from the answer text. A unit test pins that subset relationship so the
two modules cannot drift.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class EligibilityKey(StrEnum):
    """Fixed-question ``source_field`` keys whose answer is AI-extracted."""

    SALARY = "salary"
    NOTICE_PERIOD = "notice_period"
    VISA = "visa"
    EMPLOYMENT_TYPE = "employment_type"
    WORK_MODE = "work_mode"


# Membership set used by the finalize path to decide whether a just-answered
# fixed question should be run through the extractor.
EXTRACTABLE_FIXED_KEYS: Final[frozenset[str]] = frozenset(k.value for k in EligibilityKey)


# Short, human-readable note attached to an extraction when its primary value
# could not be determined from the answer — an evasive, off-topic, or implausible
# reply. The FE renders this in place of a bare null so HR sees *why* a value is
# missing instead of a silent blank. Deterministic (no LLM call).
ELIGIBILITY_NOT_STATED_NOTE: Final[dict[EligibilityKey, str]] = {
    EligibilityKey.SALARY: "No salary stated",
    EligibilityKey.NOTICE_PERIOD: "No notice period stated",
    EligibilityKey.VISA: "No visa status stated",
    EligibilityKey.EMPLOYMENT_TYPE: "No clear answer stated",
    EligibilityKey.WORK_MODE: "No clear answer stated",
}
