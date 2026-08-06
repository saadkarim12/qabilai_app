"""WhatsApp screening conversation — state machine + persistence (Step 5.3).

This module owns the whole candidate screening chat:

* :func:`begin_screening` — invoked when HR moves an application into the
  ``whatsapp`` stage, and again if HR reactivates a closed one. Sends the
  "are you still interested?" greeting (a *welcome-back* variant on
  reactivation) with Yes/No buttons, opens — or re-opens — a
  :class:`WhatsAppConversation`, and records the outbound greeting. Idempotent
  while a conversation is still open: a redelivered invite can't double-message.

* :func:`handle_inbound` — invoked (via a Celery task) for every inbound
  webhook message. Finds the open conversation for the sender, dedupes against
  redelivered ``wamid``s, records the message, and advances the state machine:

      awaiting_interest --Yes--> asking_questions --(answers)--> completed
      awaiting_interest --No---> declined (application auto-rejected)

  On a No tap the application is system-rejected (``whatsapp_declined``) and a
  closing message is sent. Messages on a terminal conversation are recorded for
  the transcript but drive nothing.

* :func:`finalize_buffered_answer` — the score-and-advance half of the
  question loop. While ``asking_questions``, each inbound bubble is only
  *recorded* (tagged with the current question index); the task then schedules
  this finalize after a short debounce. Because candidates routinely split one
  answer across several WhatsApp bubbles, finalizing only once no newer message
  has arrived lets us coalesce them into a single answer (scored once) instead
  of consuming a question per bubble and racing the candidate ahead. On the
  last answer the conversation is ``completed``.

Message **content** is persisted (``whatsapp_messages.body`` / button payloads)
because HR renders the verbatim transcript on the FE — but, as everywhere
else, it is never written to logs. Log records carry only ids / types / state.

At-least-once caveat: a webhook is processed in a single transaction (record
inbound → act → record outbound → commit). If the commit fails after we've
already called Meta, the redelivered webhook re-runs and could re-send. The
``wa_message_id`` unique index makes the common redelivery path a clean skip;
the residual double-send window is acceptable for this internal-tool volume.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models.application import Application
from src.db.models.candidate import Candidate
from src.db.models.job import Job
from src.db.models.whatsapp import WhatsAppConversation, WhatsAppMessage
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.audit import AuditAction
from src.enums.whatsapp import (
    BUTTON_TITLE_NO,
    BUTTON_TITLE_YES,
    DECLINE_REASON,
    INTEREST_REPROMPT_MESSAGE,
    QUESTION_PROMPT_TEMPLATE,
    QUESTION_TEXT_FIELD,
    QUESTIONS_INTRO_MESSAGE,
    SCREENING_COMPLETED_MESSAGE,
    SCREENING_DECLINED_MESSAGE,
    SCREENING_GREETING_TEMPLATE,
    SCREENING_REACTIVATED_GREETING_TEMPLATE,
    SCREENING_TALENT_POOL_GREETING_TEMPLATE,
    WhatsAppButtonId,
    WhatsAppConversationState,
    WhatsAppDirection,
    WhatsAppMessageType,
)
from src.enums.whatsapp_eligibility import (
    ELIGIBILITY_NOT_STATED_NOTE,
    EXTRACTABLE_FIXED_KEYS,
    EligibilityKey,
)
from src.exceptions import WhatsAppConversationNotFoundError
from src.integrations.whatsapp_client import (
    InboundMessage,
    ReplyButton,
    send_interactive_buttons,
    send_template,
    send_text,
)
from src.schemas.whatsapp import (
    WhatsAppConversationResponse,
    WhatsAppMessageResponse,
)
from src.schemas.whatsapp_eligibility import EligibilityExtraction
from src.services import (
    application_service,
    whatsapp_answer_scorer,
    whatsapp_eligibility_extractor,
)

logger = logging.getLogger(__name__)

# Conversation states that still accept inbound messages as flow input. A
# message on any other state is transcript-only.
_OPEN_STATES: Final[frozenset[WhatsAppConversationState]] = frozenset(
    {
        WhatsAppConversationState.AWAITING_INTEREST,
        WhatsAppConversationState.ASKING_QUESTIONS,
    }
)


@dataclass(frozen=True, slots=True)
class InboundOutcome:
    """Result of routing one inbound message, for the task layer to act on.

    ``conversation`` is the row a message was recorded onto (``None`` when
    nothing was persisted — a deduped redelivery or an unknown sender); the
    caller fires the realtime nudge only when it's set. ``finalize_trigger_id``
    is set when the message was *buffered* as a (possibly partial) answer: the
    task then schedules a debounced finalize keyed by this ``wamid``, so rapid
    follow-up bubbles coalesce into one answer before we score and advance.
    """

    conversation: WhatsAppConversation | None
    finalize_trigger_id: str | None = None


# --- Small helpers ----------------------------------------------------------


def _phone_variants(raw: str) -> list[str]:
    """Both stored forms a webbook ``from`` could match: ``+<digits>`` and bare.

    We store ``candidate.phone_e164`` / ``conversation.phone_e164`` as
    ``+<countrycode><number>``; Meta reports the sender without the ``+``.
    """
    bare = raw.strip().removeprefix("+")
    return [f"+{bare}", bare]


def _sorted_questions(job: Job) -> list[dict[str, Any]]:
    """The job's screening questions in HR-defined ``order``."""
    questions = job.whatsapp_questions or []
    return sorted(questions, key=lambda q: q.get("order", 0))


def _question_text(question: dict[str, Any]) -> str:
    """The candidate-facing text for one stored question."""
    text = question.get(QUESTION_TEXT_FIELD) or question.get("question_en")
    return text if isinstance(text, str) and text else "(question unavailable)"


def _question_prompt(questions: list[dict[str, Any]], index: int) -> str:
    """The full candidate-facing body for the question at ``index`` (0-based)."""
    return QUESTION_PROMPT_TEMPLATE.format(
        number=index + 1,
        total=len(questions),
        question=_question_text(questions[index]),
    )


async def _record_message(
    session: AsyncSession,
    conversation: WhatsAppConversation,
    *,
    direction: WhatsAppDirection,
    message_type: str,
    wa_message_id: str | None,
    body: str | None = None,
    button_id: str | None = None,
    button_title: str | None = None,
    question_index: int | None = None,
) -> WhatsAppMessage:
    """Append one row to the conversation transcript."""
    message = WhatsAppMessage(
        conversation_id=conversation.id,
        direction=direction,
        message_type=message_type,
        wa_message_id=wa_message_id,
        body=body,
        button_id=button_id,
        button_title=button_title,
        question_index=question_index,
    )
    session.add(message)
    await session.flush()
    return message


async def _send_and_record_text(
    session: AsyncSession,
    conversation: WhatsAppConversation,
    body: str,
    *,
    question_index: int | None = None,
) -> None:
    """Send a plain-text WhatsApp message and persist it as an outbound row."""
    wa_message_id = await send_text(conversation.phone_e164, body)
    await _record_message(
        session,
        conversation,
        direction=WhatsAppDirection.OUTBOUND,
        message_type=WhatsAppMessageType.TEXT.value,
        wa_message_id=wa_message_id,
        body=body,
        question_index=question_index,
    )


# --- Invite (state entry) ---------------------------------------------------


async def begin_screening(session: AsyncSession, *, application_id: uuid.UUID) -> None:
    """Open (or re-open) the screening conversation for ``application_id``.

    Three cases:

    * **First contact** — no conversation yet: create one and send the standard
      greeting (:data:`SCREENING_GREETING_TEMPLATE`).
    * **Already in progress** — a conversation exists and is still open
      (awaiting interest / asking questions): no-op, so a stale or duplicate
      enqueue can't double-message.
    * **Reactivation** — a conversation exists but is terminal (declined /
      completed) and HR has put the application back to ``active``: reset the
      same conversation to ``awaiting_interest`` and send the *welcome-back*
      greeting (:data:`SCREENING_REACTIVATED_GREETING_TEMPLATE`). The unique-
      per-application constraint means we reuse the row, so the full transcript
      (including the prior decline) is preserved.

    Self-skips (logs and returns) when the application is gone, not in the
    ``whatsapp`` stage, inactive, or the candidate has no phone. A genuine Meta
    send error propagates to the caller.
    """
    application = await session.get(Application, application_id)
    if application is None:
        logger.warning(
            "whatsapp.invite.application_missing",
            extra={"application_id": str(application_id)},
        )
        return

    if application.stage is not ApplicationStage.WHATSAPP:
        logger.info(
            "whatsapp.invite.skipped_stage",
            extra={"application_id": str(application_id), "stage": application.stage.value},
        )
        return
    if application.status is not ApplicationStatus.ACTIVE:
        logger.info(
            "whatsapp.invite.skipped_status",
            extra={"application_id": str(application_id), "status": application.status.value},
        )
        return

    conversation = (
        await session.execute(
            sa.select(WhatsAppConversation)
            .where(WhatsAppConversation.application_id == application_id)
            .with_for_update()
        )
    ).scalar_one_or_none()

    # Already mid-conversation — don't re-greet.
    if conversation is not None and conversation.state in _OPEN_STATES:
        logger.info(
            "whatsapp.invite.already_started",
            extra={"application_id": str(application_id), "state": conversation.state.value},
        )
        return

    candidate = await session.get(Candidate, application.candidate_id)
    if candidate is None or not candidate.phone_e164:
        logger.warning(
            "whatsapp.invite.no_phone",
            extra={"application_id": str(application_id)},
        )
        return

    job = await session.get(Job, application.job_id)
    if job is None:
        logger.warning(
            "whatsapp.invite.job_missing",
            extra={"application_id": str(application_id)},
        )
        return

    # Greeting precedence: reactivation (a closed conversation re-opened) wins;
    # otherwise a first contact uses the talent-pool wording when HR sourced the
    # candidate, else the standard "thanks for applying" greeting.
    is_reactivation = conversation is not None
    if is_reactivation:
        template = SCREENING_REACTIVATED_GREETING_TEMPLATE
    elif application.sourced_from_talent_pool:
        template = SCREENING_TALENT_POOL_GREETING_TEMPLATE
    else:
        template = SCREENING_GREETING_TEMPLATE
    greeting = template.format(
        candidate_name=candidate.full_name,
        job_title=job.title,
        company=job.hiring_company,
    )
    if settings.meta_wa_invite_template_name:
        # Prod path: a cold candidate (hasn't messaged us) is outside the 24h
        # customer-service window, so the first contact must be a pre-approved
        # template. Body params fill the template's {{1}}/{{2}}/{{3}}; the two
        # quick-reply buttons carry the same Yes/No routing payloads the
        # interactive path uses, so inbound routing is identical.
        wa_message_id = await send_template(
            candidate.phone_e164,
            template_name=settings.meta_wa_invite_template_name,
            language=settings.meta_wa_invite_template_language,
            body_params=[candidate.full_name, job.title, job.hiring_company],
            quick_reply_payloads=[
                WhatsAppButtonId.INTEREST_YES.value,
                WhatsAppButtonId.INTEREST_NO.value,
            ],
        )
        invite_message_type = WhatsAppMessageType.TEMPLATE_BUTTONS.value
    else:
        # Dev / inside an open window: free-form interactive buttons are allowed.
        buttons = [
            ReplyButton(id=WhatsAppButtonId.INTEREST_YES.value, title=BUTTON_TITLE_YES),
            ReplyButton(id=WhatsAppButtonId.INTEREST_NO.value, title=BUTTON_TITLE_NO),
        ]
        wa_message_id = await send_interactive_buttons(candidate.phone_e164, greeting, buttons)
        invite_message_type = WhatsAppMessageType.INTERACTIVE_BUTTONS.value

    if conversation is None:
        conversation = WhatsAppConversation(
            application_id=application.id,
            candidate_id=candidate.id,
            job_id=job.id,
            state=WhatsAppConversationState.AWAITING_INTEREST,
            current_question_index=None,
            phone_e164=candidate.phone_e164,
        )
        session.add(conversation)
        await session.flush()
    else:
        # Reactivation: reset the cursor but keep the transcript (a fresh run's
        # answers accumulate from empty; the prior messages remain on record).
        conversation.state = WhatsAppConversationState.AWAITING_INTEREST
        conversation.current_question_index = None
        conversation.answers = []
        conversation.closed_at = None
        conversation.phone_e164 = candidate.phone_e164
        await session.flush()

    await _record_message(
        session,
        conversation,
        direction=WhatsAppDirection.OUTBOUND,
        message_type=invite_message_type,
        wa_message_id=wa_message_id,
        body=greeting,
    )

    logger.info(
        "whatsapp.invite.reactivated" if is_reactivation else "whatsapp.invite.sent",
        extra={
            "application_id": str(application.id),
            "conversation_id": str(conversation.id),
            "job_id": str(job.id),
            "wa_message_id": wa_message_id,
        },
    )


# --- Inbound routing --------------------------------------------------------


async def handle_inbound(session: AsyncSession, *, inbound: InboundMessage) -> InboundOutcome:
    """Route one inbound webhook message into the screening state machine.

    Returns an :class:`InboundOutcome`. ``conversation`` is ``None`` when
    nothing was persisted (a deduped redelivery or a sender with no
    conversation) — the caller skips the realtime nudge then. When the message
    is buffered as a screening answer, ``finalize_trigger_id`` carries its
    ``wamid`` so the task schedules a debounced finalize (rapid follow-up
    bubbles coalesce into one answer before we score and advance).
    """
    # Dedupe redelivered webhooks before any side effect.
    already = (
        await session.execute(
            sa.select(WhatsAppMessage.id).where(WhatsAppMessage.wa_message_id == inbound.message_id)
        )
    ).scalar_one_or_none()
    if already is not None:
        logger.info(
            "whatsapp.inbound.duplicate",
            extra={"wa_message_id": inbound.message_id},
        )
        return InboundOutcome(conversation=None)

    conversation = await _find_conversation_for_sender(session, inbound.from_number)
    if conversation is None:
        # No conversation for this number at all — an unsolicited message from
        # someone we never screened. Nothing to hang it on; record the miss.
        logger.info(
            "whatsapp.inbound.no_conversation",
            extra={"from": inbound.from_number, "wa_message_id": inbound.message_id},
        )
        return InboundOutcome(conversation=None)

    if conversation.state is WhatsAppConversationState.AWAITING_INTEREST:
        await _route_interest_reply(session, conversation, inbound)
    elif conversation.state is WhatsAppConversationState.ASKING_QUESTIONS:
        trigger_id = await _buffer_question_answer(session, conversation, inbound)
        return InboundOutcome(conversation=conversation, finalize_trigger_id=trigger_id)
    else:
        # Terminal conversation (completed / declined): keep the transcript
        # complete but take no flow action.
        await _record_inbound(session, conversation, inbound)
        logger.info(
            "whatsapp.inbound.on_closed",
            extra={
                "conversation_id": str(conversation.id),
                "state": conversation.state.value,
                "wa_message_id": inbound.message_id,
            },
        )

    return InboundOutcome(conversation=conversation)


async def _find_conversation_for_sender(
    session: AsyncSession, from_number: str
) -> WhatsAppConversation | None:
    """The conversation a sender's message belongs to, open ones preferred.

    Ordering puts **open** conversations (awaiting interest / asking questions)
    ahead of terminal ones, then most-recent first. So an active screening wins
    routing, but if the candidate only has a closed (e.g. declined)
    conversation we still return it — the caller records the message for the
    transcript yet sends no reply. ``FOR UPDATE`` serializes rapid messages
    from the same candidate against the same cursor.
    """
    is_open = WhatsAppConversation.state.in_([s.value for s in _OPEN_STATES])
    stmt = (
        sa.select(WhatsAppConversation)
        .where(WhatsAppConversation.phone_e164.in_(_phone_variants(from_number)))
        .order_by(is_open.desc(), WhatsAppConversation.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _inbound_type(inbound: InboundMessage) -> str:
    """Persisted ``message_type`` for an inbound row."""
    if inbound.button_reply_id is not None:
        return WhatsAppMessageType.BUTTON_REPLY.value
    return WhatsAppMessageType.TEXT.value


async def _record_inbound(
    session: AsyncSession,
    conversation: WhatsAppConversation,
    inbound: InboundMessage,
    *,
    question_index: int | None = None,
) -> WhatsAppMessage:
    """Persist an inbound message onto the transcript."""
    return await _record_message(
        session,
        conversation,
        direction=WhatsAppDirection.INBOUND,
        message_type=_inbound_type(inbound),
        wa_message_id=inbound.message_id,
        body=inbound.text,
        button_id=inbound.button_reply_id,
        button_title=inbound.button_reply_title,
        question_index=question_index,
    )


async def _route_interest_reply(
    session: AsyncSession,
    conversation: WhatsAppConversation,
    inbound: InboundMessage,
) -> None:
    """Handle a reply to the Yes/No interest prompt."""
    await _record_inbound(session, conversation, inbound)

    if inbound.button_reply_id == WhatsAppButtonId.INTEREST_NO.value:
        await _decline(session, conversation)
        return
    if inbound.button_reply_id == WhatsAppButtonId.INTEREST_YES.value:
        await _confirm_interest(session, conversation)
        return

    # Anything other than a Yes/No tap — nudge and stay put.
    await _send_and_record_text(session, conversation, INTEREST_REPROMPT_MESSAGE)
    logger.info(
        "whatsapp.inbound.interest_unrecognized",
        extra={"conversation_id": str(conversation.id)},
    )


async def _decline(session: AsyncSession, conversation: WhatsAppConversation) -> None:
    """Candidate tapped No: auto-reject the application and close the chat."""
    application = await session.get(Application, conversation.application_id)
    if application is not None:
        await application_service.system_reject_application(
            session,
            application=application,
            reason=DECLINE_REASON,
            action=AuditAction.WHATSAPP_DECLINED,
        )

    conversation.state = WhatsAppConversationState.DECLINED
    conversation.closed_at = datetime.now(UTC)
    await session.flush()

    await _send_and_record_text(session, conversation, SCREENING_DECLINED_MESSAGE)
    logger.info(
        "whatsapp.screening.declined",
        extra={
            "conversation_id": str(conversation.id),
            "application_id": str(conversation.application_id),
        },
    )


async def _confirm_interest(session: AsyncSession, conversation: WhatsAppConversation) -> None:
    """Candidate tapped Yes: start asking the job's screening questions."""
    job = await session.get(Job, conversation.job_id)
    questions = _sorted_questions(job) if job is not None else []

    if not questions:
        # Nothing to ask — confirm and close.
        conversation.state = WhatsAppConversationState.COMPLETED
        conversation.current_question_index = None
        conversation.closed_at = datetime.now(UTC)
        await session.flush()
        await _send_and_record_text(session, conversation, SCREENING_COMPLETED_MESSAGE)
        logger.info(
            "whatsapp.screening.completed_no_questions",
            extra={"conversation_id": str(conversation.id)},
        )
        return

    conversation.state = WhatsAppConversationState.ASKING_QUESTIONS
    conversation.current_question_index = 0
    await session.flush()

    # Send the intro and the first question as a *single* message so the
    # candidate doesn't receive two bubbles back-to-back on tapping Yes.
    first_message = f"{QUESTIONS_INTRO_MESSAGE}\n\n{_question_prompt(questions, 0)}"
    await _send_and_record_text(session, conversation, first_message, question_index=0)
    logger.info(
        "whatsapp.screening.questions_started",
        extra={
            "conversation_id": str(conversation.id),
            "question_count": len(questions),
        },
    )


async def _score_answer_safely(
    *, question_text: str, answer_text: str
) -> tuple[int | None, int | None, str | None]:
    """Score one answer, swallowing every failure into nulls.

    Scoring is a best-effort enrichment on the inbound hot path: a Claude
    outage, a bind failure, or an empty answer must never break the screening
    conversation. Returns ``(relevance, ai_likelihood, rationale)`` — all
    ``None`` when the answer is blank or scoring couldn't produce a result.
    No question/answer/rationale content is logged.
    """
    if not answer_text.strip():
        return None, None, None
    try:
        score = await whatsapp_answer_scorer.score_answer(
            question=question_text, answer=answer_text
        )
    except Exception:
        logger.warning("whatsapp.answer_score.failed", exc_info=False)
        return None, None, None
    if score is None:
        return None, None, None
    return score.relevance_score, score.ai_likelihood_score, score.rationale


def _normalize_extraction(
    source_field: str, job: Job | None, ex: EligibilityExtraction
) -> dict[str, Any]:
    """Shape the model's extraction into the per-field dict the FE renders.

    For employment-type / work-mode the display value is the *job's* setting
    (e.g. "Full Time" / "Remote") — the answer only tells us whether the
    candidate accepted it — so we fold the job value in here where it's known.

    When the answer's primary value can't be determined (evasive/off-topic reply,
    or an implausible figure dropped to null upstream), a short ``note`` is
    attached so HR sees *why* the value is blank rather than a silent null.
    """
    try:
        key = EligibilityKey(source_field)
    except ValueError:
        return {}

    if key is EligibilityKey.SALARY:
        result: dict[str, Any] = {
            "amount": ex.salary_amount,
            "currency": ex.salary_currency or (job.currency if job is not None else None),
        }
        primary = ex.salary_amount
    elif key is EligibilityKey.NOTICE_PERIOD:
        result = {"days": ex.notice_period_days}
        primary = ex.notice_period_days
    elif key is EligibilityKey.VISA:
        result = {"valid": ex.visa_valid}
        primary = ex.visa_valid
    elif key is EligibilityKey.EMPLOYMENT_TYPE:
        result = {
            "accepted": ex.accepted,
            "value": job.employment_type.value if job is not None else None,
        }
        primary = ex.accepted
    else:  # EligibilityKey.WORK_MODE
        result = {
            "accepted": ex.accepted,
            "value": job.work_mode.value if job is not None else None,
        }
        primary = ex.accepted

    if primary is None:
        result["note"] = ELIGIBILITY_NOT_STATED_NOTE[key]
    return result


async def _extract_eligibility_safely(
    *, question: dict[str, Any], job: Job | None, question_text: str, answer_text: str
) -> dict[str, Any] | None:
    """Best-effort normalized extraction for a fixed eligibility question.

    Runs only for the extractable fixed questions (salary / notice / visa /
    employment / work-mode); the commitment verdict is derived from state, not
    extracted here. Any failure (non-extractable question, blank answer, Claude
    outage, bind failure) returns ``None`` so the row is simply left
    un-normalized and the screening flow continues.
    """
    source_field = question.get("source_field")
    if source_field not in EXTRACTABLE_FIXED_KEYS or not answer_text.strip():
        return None
    try:
        ex = await whatsapp_eligibility_extractor.extract_fixed_answer(
            source_field=source_field, question=question_text, answer=answer_text
        )
    except Exception:
        logger.warning("whatsapp.eligibility.failed", exc_info=False)
        return None
    if ex is None:
        return None
    return _normalize_extraction(source_field, job, ex)


async def _buffer_question_answer(
    session: AsyncSession,
    conversation: WhatsAppConversation,
    inbound: InboundMessage,
) -> str | None:
    """Record an inbound message against the current question, *without* advancing.

    The actual score-and-advance is deferred to :func:`finalize_buffered_answer`,
    run after a debounce window, so several quick bubbles coalesce into one
    answer. Returns the ``wamid`` to key the debounced finalize on, or ``None``
    when the cursor is already past the last question (we complete inline then).
    """
    job = await session.get(Job, conversation.job_id)
    questions = _sorted_questions(job) if job is not None else []
    index = conversation.current_question_index or 0

    if index >= len(questions):
        # Cursor past the end (shouldn't happen) — treat as complete now.
        await _record_inbound(session, conversation, inbound)
        await _complete(session, conversation)
        return None

    await _record_inbound(session, conversation, inbound, question_index=index)
    return inbound.message_id


async def finalize_buffered_answer(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    trigger_wa_message_id: str,
) -> WhatsAppConversation | None:
    """Coalesce the buffered bubbles for the current question into one answer.

    Runs after the debounce window. Self-skips (returns ``None``) when the
    conversation has since moved on, or when a *newer* inbound message has
    arrived after ``trigger_wa_message_id`` — in that case this finalize is
    stale and the newer message's own finalize will fold everything in. When it
    does run, it concatenates every inbound bubble recorded against the current
    question, scores the combined answer, advances the cursor, and sends the
    next question (or completes).
    """
    conversation = (
        await session.execute(
            sa.select(WhatsAppConversation)
            .where(WhatsAppConversation.id == conversation_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if conversation is None or conversation.state is not WhatsAppConversationState.ASKING_QUESTIONS:
        return None
    index = conversation.current_question_index
    if index is None:
        return None

    # Debounce: only finalize if our trigger is still the most recent inbound.
    latest = (
        await session.execute(
            sa.select(WhatsAppMessage)
            .where(
                WhatsAppMessage.conversation_id == conversation.id,
                WhatsAppMessage.direction == WhatsAppDirection.INBOUND,
            )
            .order_by(WhatsAppMessage.created_at.desc(), WhatsAppMessage.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None or latest.wa_message_id != trigger_wa_message_id:
        logger.info(
            "whatsapp.answer.debounce_superseded",
            extra={"conversation_id": str(conversation.id), "wa_message_id": trigger_wa_message_id},
        )
        return None

    job = await session.get(Job, conversation.job_id)
    questions = _sorted_questions(job) if job is not None else []
    if index >= len(questions):
        await _complete(session, conversation)
        return conversation

    # Gather every bubble recorded against this question, in arrival order.
    fragments = (
        (
            await session.execute(
                sa.select(WhatsAppMessage)
                .where(
                    WhatsAppMessage.conversation_id == conversation.id,
                    WhatsAppMessage.direction == WhatsAppDirection.INBOUND,
                    WhatsAppMessage.question_index == index,
                )
                .order_by(WhatsAppMessage.created_at.asc(), WhatsAppMessage.id.asc())
            )
        )
        .scalars()
        .all()
    )
    parts = [piece for f in fragments if (piece := (f.body or f.button_title or "").strip())]
    answer_text = "\n".join(parts)

    question = questions[index]
    question_text = _question_text(question)

    # Only AI-authored background-validation questions are AI-scored; fixed and
    # custom questions just store the answer verbatim (scores stay null). The
    # scoring itself is best-effort — a failure leaves scores null and the
    # conversation still advances. The score lands on the final bubble.
    if question.get("ai_verifies_response"):
        relevance, ai_likelihood, rationale = await _score_answer_safely(
            question_text=question_text, answer_text=answer_text
        )
    else:
        relevance, ai_likelihood, rationale = None, None, None
    latest.answer_relevance_score = relevance
    latest.answer_ai_score = ai_likelihood
    latest.answer_score_rationale = rationale

    # Normalize the fixed eligibility answers (salary / notice / visa / …) into a
    # small structured value the score card renders. Best-effort: a miss leaves
    # ``extracted`` null and the flow continues.
    extracted = await _extract_eligibility_safely(
        question=question, job=job, question_text=question_text, answer_text=answer_text
    )

    conversation.answers = [
        *conversation.answers,
        {
            "question_id": question.get("id"),
            "question": question_text,
            "answer": answer_text,
            "relevance_score": relevance,
            "ai_likelihood_score": ai_likelihood,
            "rationale": rationale,
            "extracted": extracted,
        },
    ]
    await session.flush()

    next_index = index + 1
    if next_index < len(questions):
        conversation.current_question_index = next_index
        await session.flush()
        await _send_question(session, conversation, questions, next_index)
        return conversation

    await _complete(session, conversation)
    return conversation


async def _send_question(
    session: AsyncSession,
    conversation: WhatsAppConversation,
    questions: list[dict[str, Any]],
    index: int,
) -> None:
    """Send the question at ``index`` (0-based) with progress framing."""
    await _send_and_record_text(
        session, conversation, _question_prompt(questions, index), question_index=index
    )


async def _complete(session: AsyncSession, conversation: WhatsAppConversation) -> None:
    """Mark the conversation completed and send the closing message."""
    conversation.state = WhatsAppConversationState.COMPLETED
    conversation.current_question_index = None
    conversation.closed_at = datetime.now(UTC)
    await session.flush()
    await _send_and_record_text(session, conversation, SCREENING_COMPLETED_MESSAGE)
    logger.info(
        "whatsapp.screening.completed",
        extra={
            "conversation_id": str(conversation.id),
            "application_id": str(conversation.application_id),
            "answer_count": len(conversation.answers),
        },
    )


# --- HR read ----------------------------------------------------------------


async def get_conversation_detail(
    session: AsyncSession, application_id: uuid.UUID
) -> WhatsAppConversationResponse:
    """Return the screening conversation + ordered transcript for HR.

    Raises :class:`WhatsAppConversationNotFoundError` (404) when the
    application has no conversation yet.
    """
    conversation = (
        await session.execute(
            sa.select(WhatsAppConversation).where(
                WhatsAppConversation.application_id == application_id
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise WhatsAppConversationNotFoundError(
            f"No WhatsApp conversation for application {application_id}"
        )

    message_rows = (
        (
            await session.execute(
                sa.select(WhatsAppMessage)
                .where(WhatsAppMessage.conversation_id == conversation.id)
                .order_by(WhatsAppMessage.created_at.asc(), WhatsAppMessage.id.asc())
            )
        )
        .scalars()
        .all()
    )

    return WhatsAppConversationResponse(
        id=conversation.id,
        application_id=conversation.application_id,
        candidate_id=conversation.candidate_id,
        job_id=conversation.job_id,
        state=conversation.state,
        current_question_index=conversation.current_question_index,
        answers=conversation.answers,
        phone_e164=conversation.phone_e164,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        closed_at=conversation.closed_at,
        messages=[WhatsAppMessageResponse.model_validate(m) for m in message_rows],
    )
