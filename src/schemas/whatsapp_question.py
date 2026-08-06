"""Pydantic schemas for AI-generated WhatsApp screening questions.

Two shapes live here:

* :class:`WhatsAppQuestionDraft` is what Claude returns in each item of the
  generation response — *content only*. No ``id`` or ``order`` — those are
  HR-facing identifiers the service mints, not the model.
* :class:`WhatsAppQuestion` is the final shape stored in
  ``Job.whatsapp_questions`` JSONB. It extends the draft with the
  service-assigned ``id`` and ``order`` plus the provenance fields
  ``is_ai_generated`` / ``source_field``.

Field-length caps below are sanity limits, not UX rules. The WhatsApp side
will trim further for the message-send window in Step 5.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from src.enums.whatsapp_questions import QuestionCategory


class WhatsAppQuestionDraft(BaseModel):
    """Content-only shape Claude returns for one screening question.

    ``extra='forbid'`` so a stray ``id`` from Claude doesn't silently leak
    into the stored object — the service is the only place IDs are minted.
    """

    model_config = ConfigDict(extra="forbid")

    category: QuestionCategory
    subcategory: str = Field(min_length=1, max_length=80)
    question_en: str = Field(min_length=1, max_length=400)
    question_ar: str = Field(min_length=1, max_length=400)
    reasoning: str = Field(min_length=1, max_length=400)


class WhatsAppQuestionDraftList(BaseModel):
    """Wrapper Claude returns from the generation prompt: a list of drafts.

    Tool-use structured output binds to a single model, so the ``questions``
    array is wrapped here. Non-empty and length-cap rules are intentionally
    enforced in the service (explicit empty check + lenient truncation), not on
    this schema, so an over-long response is trimmed rather than rejected.
    ``extra='forbid'`` rejects stray top-level keys.
    """

    model_config = ConfigDict(extra="forbid")

    questions: list[WhatsAppQuestionDraft]


class WhatsAppQuestion(WhatsAppQuestionDraft):
    """Final, JSONB-stored WhatsApp screening question.

    * ``id`` is minted by ``whatsapp_question_generator`` (``q_`` + 8
      url-safe chars). Stable for the life of the job.
    * ``order`` is 1-based positional order — what HR will see in the list.
    * ``is_ai_generated`` is True for the AI-authored background-validation
      questions; the fixed questions and HR-added custom questions set it False.
    * ``source_field`` tags a fixed question with its template key (e.g.
      ``"salary"``, ``"work_mode"``) so the UI can render it as a locked
      "Fixed" question. ``None`` for AI and custom questions.
    * ``ai_verifies_response`` gates answer scoring: only questions with this
      True have the candidate's reply scored by Claude (the AI
      background-validation questions). Fixed + custom answers are stored
      verbatim, never AI-scored.
    """

    id: str = Field(pattern=r"^q_[A-Za-z0-9_-]{8}$")
    order: int = Field(ge=1, le=15)
    is_ai_generated: bool = True
    source_field: str | None = None
    ai_verifies_response: bool = False
