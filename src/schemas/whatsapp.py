"""Pydantic response shapes for the WhatsApp screening conversation.

Over-the-wire contract for ``GET /applications/{id}/whatsapp`` — the HR-facing
transcript HR renders on the application detail screen. The conversation
carries its state-machine cursor, the structured Q&A copy, and the full
ordered list of messages (the "what was really said" record).

Message ``body`` / button payloads are candidate content surfaced here on
purpose: HR needs to read the real conversation. (They're still never logged.)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from src.enums.whatsapp import WhatsAppConversationState, WhatsAppDirection


class WhatsAppMessageResponse(BaseModel):
    """One message in the transcript, oldest-first in the parent list."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    direction: WhatsAppDirection
    message_type: str
    body: str | None
    button_id: str | None
    button_title: str | None
    question_index: int | None
    wa_message_id: str | None
    # Per-answer scores (set only on inbound free-text answer rows; null
    # elsewhere). HR renders these beside the message in the chat view.
    answer_relevance_score: int | None
    answer_ai_score: int | None
    answer_score_rationale: str | None
    created_at: datetime


class WhatsAppConversationResponse(BaseModel):
    """The screening conversation for one application, with its transcript."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    state: WhatsAppConversationState
    current_question_index: int | None
    answers: list[dict[str, object]]
    phone_e164: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    messages: list[WhatsAppMessageResponse]
