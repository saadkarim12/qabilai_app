"""WhatsApp screening-conversation ORM models (Step 5.3).

Two tables back the candidate screening chat:

* :class:`WhatsAppConversation` — one row per application's screening
  conversation. Holds the **state-machine cursor** (``state`` +
  ``current_question_index``) the inbound router advances, plus an
  ``answers`` JSONB convenience copy of the structured Q&A for review.
  Unique on ``application_id`` (one conversation per application);
  ``candidate_id`` / ``job_id`` are denormalized so HR can read a
  candidate's whole WhatsApp history without walking applications.

* :class:`WhatsAppMessage` — the **verbatim transcript**, one row per
  message in either direction. This is the source of truth HR renders on
  the FE ("what was the real conversation?"). Unlike logs — which never
  carry message content (PII) — these rows intentionally store ``body`` /
  button payloads because showing them to HR is the product requirement.
  ``wa_message_id`` is uniquely indexed so a redelivered Meta webhook
  doesn't double-insert (and so the router can self-skip a dup).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    UUID,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base
from src.enums.whatsapp import (
    WhatsAppConversationState,
    WhatsAppDirection,
)

PHONE_E164_MAX_LENGTH = 20
WA_MESSAGE_ID_MAX_LENGTH = 128  # Meta ``wamid.*`` ids; generous headroom.
MESSAGE_TYPE_MAX_LENGTH = 32
BUTTON_ID_MAX_LENGTH = 256
BUTTON_TITLE_MAX_LENGTH = 64


def _enum_column(enum_cls: type[StrEnum]) -> Enum:
    """Varchar-backed enum column that stores ``.value`` (not ``.name``)."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=32,
        create_constraint=False,
        values_callable=lambda cls: [e.value for e in cls],
    )


class WhatsAppConversation(Base):
    """One application's screening conversation + its state-machine cursor."""

    __tablename__ = "whatsapp_conversations"
    __table_args__ = (
        UniqueConstraint(
            "application_id",
            name="uq_whatsapp_conversations_application",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, server_default=text("gen_random_uuid()")
    )

    # --- FKs ----------------------------------------------------------------
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey(
            "applications.id",
            ondelete="CASCADE",
            name="fk_whatsapp_conversations_application",
        ),
        nullable=False,
    )
    # Denormalized for the per-candidate history read; CASCADE so deleting a
    # candidate removes their conversations.
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey(
            "candidates.id",
            ondelete="CASCADE",
            name="fk_whatsapp_conversations_candidate",
        ),
        nullable=False,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("jobs.id", ondelete="CASCADE", name="fk_whatsapp_conversations_job"),
        nullable=False,
    )

    # --- State machine ------------------------------------------------------
    state: Mapped[WhatsAppConversationState] = mapped_column(
        _enum_column(WhatsAppConversationState),
        nullable=False,
        server_default=text(f"'{WhatsAppConversationState.AWAITING_INTEREST.value}'"),
    )
    # 0-based index of the question we're currently awaiting an answer to.
    # NULL until interest is confirmed (state → asking_questions).
    current_question_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Structured Q&A copy: list of ``{question_id, question, answer}``. The
    # ``whatsapp_messages`` transcript is authoritative; this is a convenience
    # shape for HR review / future scoring without re-deriving from messages.
    answers: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # E.164 snapshot of the number we're conversing with, captured at start so
    # the thread stays coherent even if the candidate row's phone later changes.
    phone_e164: Mapped[str] = mapped_column(String(PHONE_E164_MAX_LENGTH), nullable=False)

    # --- Timestamps ---------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    # Set when the conversation reaches a terminal state (completed / declined).
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"WhatsAppConversation(id={self.id!r}, application_id={self.application_id!r}, "
            f"state={self.state!r}, current_question_index={self.current_question_index!r})"
        )


class WhatsAppMessage(Base):
    """One message in a screening conversation — the verbatim HR-facing transcript."""

    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        # Dedupe redelivered Meta webhooks: the same ``wamid`` never inserts
        # twice. NULLs are allowed (and non-unique under Postgres) for the
        # rare outbound row that has no id yet.
        UniqueConstraint("wa_message_id", name="uq_whatsapp_messages_wa_message_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, server_default=text("gen_random_uuid()")
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey(
            "whatsapp_conversations.id",
            ondelete="CASCADE",
            name="fk_whatsapp_messages_conversation",
        ),
        nullable=False,
    )

    direction: Mapped[WhatsAppDirection] = mapped_column(
        _enum_column(WhatsAppDirection), nullable=False
    )
    # Meta's ``wamid.*``. Nullable so a send that somehow returns no id still
    # records the attempt; uniquely indexed for inbound-redelivery dedupe.
    wa_message_id: Mapped[str | None] = mapped_column(
        String(WA_MESSAGE_ID_MAX_LENGTH), nullable=True
    )
    message_type: Mapped[str] = mapped_column(String(MESSAGE_TYPE_MAX_LENGTH), nullable=False)

    # Candidate/our message text. PII — stored for HR display, never logged.
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Button payload (outbound: the buttons we offered are in ``body``-adjacent
    # copy; inbound: the tapped button). ``button_id`` is what we route on.
    button_id: Mapped[str | None] = mapped_column(String(BUTTON_ID_MAX_LENGTH), nullable=True)
    button_title: Mapped[str | None] = mapped_column(String(BUTTON_TITLE_MAX_LENGTH), nullable=True)
    # Which screening question this message relates to (0-based), when applicable.
    question_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Answer scoring (Step 5.4) ------------------------------------------
    # Set only on an inbound free-text answer row (NULL everywhere else, and
    # NULL on an answer if scoring failed). ``answer_relevance_score`` /
    # ``answer_ai_score`` are 0-10 integers (higher AI score = more likely
    # AI-written); ``answer_score_rationale`` is the one-line HR-facing note.
    answer_relevance_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answer_ai_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answer_score_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:
        return (
            f"WhatsAppMessage(id={self.id!r}, conversation_id={self.conversation_id!r}, "
            f"direction={self.direction!r}, message_type={self.message_type!r})"
        )
