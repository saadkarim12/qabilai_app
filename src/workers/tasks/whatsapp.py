"""Celery tasks for the WhatsApp screening conversation (Step 5.3).

Thin sync→async bridges (``asyncio.run`` over a fresh ``SessionFactory``
session, per the worker's NullPool/event-loop model) that delegate to
:mod:`src.services.whatsapp_conversation_service` and own the commit.

* ``send_whatsapp_invite_task`` — opens the conversation and greets when HR
  moves an application into the ``whatsapp`` stage. No ``autoretry_for``: the
  greeting send isn't idempotent against a partial failure (the conversation
  row guards re-entry, but an automatic retry on a transient Meta error could
  still double-message), so a failure is logged and left for HR to re-trigger.

* ``handle_whatsapp_inbound_task`` — routes one inbound webhook message into
  the state machine. The ``wa_message_id`` unique index dedupes Meta
  redeliveries, so this task is safe to re-run; we still skip ``autoretry`` and
  rely on Meta's own webhook retries to avoid surprising re-sends.
"""

from __future__ import annotations

import asyncio
import uuid

from src.config import settings
from src.db.session import SessionFactory
from src.enums.events import AppEventType
from src.integrations.whatsapp_client import InboundMessage
from src.schemas.events import AppEvent
from src.services.events_service import publish_app_event
from src.services.whatsapp_conversation_service import (
    begin_screening,
    finalize_buffered_answer,
    handle_inbound,
)
from src.workers.celery_app import SLOW_QUEUE, celery_app


async def _run_begin_screening(application_id: uuid.UUID) -> None:
    async with SessionFactory() as session:
        await begin_screening(session, application_id=application_id)
        await session.commit()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.send_whatsapp_invite",
    queue=SLOW_QUEUE,
)
def send_whatsapp_invite_task(application_id: str) -> None:
    """Open the screening conversation and send the L2 interest greeting."""
    asyncio.run(_run_begin_screening(uuid.UUID(application_id)))


async def _publish_message_nudge(application_id: uuid.UUID, job_id: uuid.UUID) -> None:
    """Best-effort realtime nudge that a conversation's transcript changed.

    The envelope is a pointer: HR refetches the transcript through the authed
    endpoint; message text never leaves the DB via this channel.
    """
    await publish_app_event(
        AppEvent(
            event=AppEventType.WHATSAPP_MESSAGE,
            application_id=application_id,
            job_id=job_id,
        )
    )


async def _run_handle_inbound(inbound: InboundMessage) -> tuple[uuid.UUID, str] | None:
    """Record/route one inbound message; return ``(conversation_id, wamid)`` when
    a debounced answer-finalize should be scheduled, else ``None``."""
    async with SessionFactory() as session:
        outcome = await handle_inbound(session, inbound=inbound)
        await session.commit()
        if outcome.conversation is None:
            return None
        await _publish_message_nudge(
            outcome.conversation.application_id,
            outcome.conversation.job_id,
        )
        if outcome.finalize_trigger_id is not None:
            return outcome.conversation.id, outcome.finalize_trigger_id
        return None


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.handle_whatsapp_inbound",
    queue=SLOW_QUEUE,
)
def handle_whatsapp_inbound_task(
    from_number: str,
    message_id: str,
    message_type: str,
    text: str | None = None,
    button_reply_id: str | None = None,
    button_reply_title: str | None = None,
) -> None:
    """Route one inbound WhatsApp message into the screening state machine.

    Receives the already-parsed :class:`InboundMessage` fields as primitives
    (Celery serializes JSON, not dataclasses) and reconstructs the value object
    for the service. When the message is a screening answer, schedules a
    debounced finalize (``countdown``) *after* the async run completes — doing
    it here (not inside ``asyncio.run``) keeps eager-mode tests from nesting
    event loops.
    """
    inbound = InboundMessage(
        from_number=from_number,
        message_id=message_id,
        type=message_type,
        text=text,
        button_reply_id=button_reply_id,
        button_reply_title=button_reply_title,
    )
    pending = asyncio.run(_run_handle_inbound(inbound))
    if pending is not None:
        conversation_id, trigger_wa_message_id = pending
        finalize_whatsapp_answer_task.apply_async(
            kwargs={
                "conversation_id": str(conversation_id),
                "trigger_wa_message_id": trigger_wa_message_id,
            },
            countdown=settings.whatsapp_answer_debounce_seconds,
        )


async def _run_finalize_answer(conversation_id: uuid.UUID, trigger_wa_message_id: str) -> None:
    async with SessionFactory() as session:
        conversation = await finalize_buffered_answer(
            session,
            conversation_id=conversation_id,
            trigger_wa_message_id=trigger_wa_message_id,
        )
        await session.commit()
        # The finalize sent the next question (or closing) — nudge HR to refetch.
        if conversation is not None:
            await _publish_message_nudge(conversation.application_id, conversation.job_id)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="kabil.finalize_whatsapp_answer",
    queue=SLOW_QUEUE,
)
def finalize_whatsapp_answer_task(conversation_id: str, trigger_wa_message_id: str) -> None:
    """Coalesce a candidate's buffered bubbles into one answer and advance.

    Scheduled with a ``countdown`` debounce by ``handle_whatsapp_inbound_task``;
    self-skips if a newer inbound message has since arrived (see
    :func:`finalize_buffered_answer`)."""
    asyncio.run(_run_finalize_answer(uuid.UUID(conversation_id), trigger_wa_message_id))
