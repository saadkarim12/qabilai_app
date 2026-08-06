"""WhatsApp screening-question categories.

A Job's ``whatsapp_questions`` JSONB column stores objects of shape
``{"category": <QuestionCategory>, "question": <str>, ...}``. Categories
let the WhatsApp agent (Step 5.x) route follow-ups and the scoring
pipeline weight answers consistently.
"""

from __future__ import annotations

from enum import StrEnum


class QuestionCategory(StrEnum):
    """Why a screening question is being asked.

    Exactly three buckets the WhatsApp list is allowed to use:

    * ``COMMITMENT`` — will the candidate actually take and stay in this role
      (location, notice period, employment type, work mode).
    * ``SALARY`` — pay expectations vs the role's band.
    * ``BACKGROUND_VALIDATION`` — verifiable hands-on experience with the
      role's listed required + preferred skills, phrased so the answer can be
      cross-checked against the CV.

    The fixed questions (see ``src.enums.fixed_screening_questions``) cover the
    ``COMMITMENT`` / ``SALARY`` topics (the fixed visa question is a
    ``COMMITMENT`` item); the AI generator authors the ``BACKGROUND_VALIDATION``
    items (the only AI-scored ones).
    """

    COMMITMENT = "commitment"
    SALARY = "salary"
    BACKGROUND_VALIDATION = "background_validation"
