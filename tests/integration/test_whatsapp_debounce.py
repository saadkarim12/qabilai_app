"""Integration tests for the WhatsApp screening answer-debounce + intro merge.

Drives :func:`handle_inbound` / :func:`finalize_buffered_answer` directly with
the per-test transactional ``db_session`` (writes roll back at teardown). The
outbound WhatsApp send and the Claude answer-scorer are monkeypatched so the
tests never touch Meta or Anthropic.

Covers the two fixes for "candidate gets two questions at once":

* Tapping Yes sends the intro + first question as a **single** message.
* Several quick inbound bubbles for one question **coalesce** into one answer
  (scored once, one cursor advance) rather than one-question-per-bubble; an
  earlier bubble's finalize is superseded by a newer arrival.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.application import Application
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.job import Job
from src.db.models.user import User
from src.db.models.whatsapp import WhatsAppConversation, WhatsAppMessage
from src.enums.applications import ApplicationStage, ApplicationStatus
from src.enums.jobs import EmploymentType, JobStatus, NoticePeriod, WorkMode
from src.enums.roles import UserRole
from src.enums.whatsapp import (
    QUESTIONS_INTRO_MESSAGE,
    WhatsAppButtonId,
    WhatsAppConversationState,
    WhatsAppDirection,
)
from src.integrations.whatsapp_client import InboundMessage
from src.schemas.whatsapp_eligibility import EligibilityExtraction
from src.services import whatsapp_answer_scorer, whatsapp_eligibility_extractor
from src.services import whatsapp_conversation_service as svc
from src.utils.password import hash_password
from src.utils.slug import generate_public_slug

pytestmark = pytest.mark.integration

PHONE = "+971500000001"
QUESTIONS = [
    {"id": "q1", "order": 0, "question_en": "What is your notice period?"},
    {"id": "q2", "order": 1, "question_en": "What are your salary expectations?"},
    {"id": "q3", "order": 2, "question_en": "Why this role?"},
]


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_conversation(
    db_session: AsyncSession,
    *,
    state: WhatsAppConversationState,
    current_question_index: int | None,
) -> WhatsAppConversation:
    user = User(
        email=f"hr-{uuid.uuid4().hex[:8]}@kabil.dev",
        password_hash=hash_password("hunter2-correct"),
        full_name="HR Person",
        role=UserRole.ADMIN,
    )
    db_session.add(user)
    await db_session.flush()

    job = Job(
        created_by=user.id,
        title="Senior Backend Engineer",
        hiring_company="Kabil Test Co",
        country="AE",
        city="Dubai",
        employment_type=EmploymentType.PERMANENT,
        work_mode=WorkMode.HYBRID,
        currency="AED",
        min_salary=20_000,
        max_salary=30_000,
        notice_period=NoticePeriod.DAYS_30,
        min_experience_years=5,
        required_skills=["Python"],
        preferred_skills=[],
        nationality_preference=[],
        languages_required=["english"],
        job_description="Build the Kabil hiring backend.",
        status=JobStatus.OPEN,
        public_slug=generate_public_slug(),
        whatsapp_questions=QUESTIONS,
    )
    db_session.add(job)
    await db_session.flush()

    cand = Candidate(
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        phone_e164=PHONE,
        full_name="Test Candidate",
        parsed_profile={"full_name": "Test Candidate", "parse_status": "ok"},
    )
    db_session.add(cand)
    await db_session.flush()

    cv = CvDocument(
        candidate_id=cand.id,
        blob_url="https://blob.invalid/test.pdf",
        blob_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        is_current=True,
        extracted_text="...",
    )
    db_session.add(cv)
    await db_session.flush()

    app = Application(
        job_id=job.id,
        candidate_id=cand.id,
        cv_document_id=cv.id,
        stage=ApplicationStage.WHATSAPP,
        status=ApplicationStatus.ACTIVE,
        consent_context={"ip": "127.0.0.1", "user_agent": "pytest"},
        consented_at=datetime.now(UTC),
    )
    db_session.add(app)
    await db_session.flush()

    conversation = WhatsAppConversation(
        application_id=app.id,
        candidate_id=cand.id,
        job_id=job.id,
        state=state,
        current_question_index=current_question_index,
        phone_e164=PHONE,
    )
    db_session.add(conversation)
    await db_session.flush()
    return conversation


def _patch_send_and_score(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub the outbound send (record bodies) and the Claude scorer (no-op)."""
    sent_bodies: list[str] = []

    async def _fake_send_text(to: str, body: str) -> str:
        sent_bodies.append(body)
        return f"wamid.out.{len(sent_bodies)}"

    async def _fake_score(*, question: str, answer: str) -> None:
        return None

    async def _fake_extract(
        *, source_field: str, question: str, answer: str
    ) -> EligibilityExtraction | None:
        return None

    monkeypatch.setattr(svc, "send_text", _fake_send_text)
    monkeypatch.setattr(whatsapp_answer_scorer, "score_answer", _fake_score)
    monkeypatch.setattr(whatsapp_eligibility_extractor, "extract_fixed_answer", _fake_extract)
    return sent_bodies


def _inbound(text: str) -> InboundMessage:
    return InboundMessage(
        from_number=PHONE.removeprefix("+"),
        message_id=f"wamid.in.{uuid.uuid4().hex}",
        type="text",
        text=text,
    )


async def _inbound_rows(
    db_session: AsyncSession, conversation_id: uuid.UUID
) -> list[WhatsAppMessage]:
    return list(
        (
            await db_session.execute(
                sa.select(WhatsAppMessage)
                .where(
                    WhatsAppMessage.conversation_id == conversation_id,
                    WhatsAppMessage.direction == WhatsAppDirection.INBOUND,
                )
                .order_by(WhatsAppMessage.created_at.asc())
            )
        )
        .scalars()
        .all()
    )


async def _answer_and_finalize(
    db_session: AsyncSession,
    conversation: WhatsAppConversation,
    text: str,
    *,
    when: datetime,
) -> WhatsAppConversation | None:
    """Deliver one single-bubble answer and finalize it deterministically.

    Postgres ``now()`` is constant within a transaction, so every inbound row a
    test inserts shares a ``created_at`` and the debounce's "is this still the
    latest inbound?" check would tie-break on random UUID order. We stamp the
    just-arrived row with a strictly-increasing ``when`` so it is unambiguously
    the newest, mirroring the real two-webhook arrival order.
    """
    out = await svc.handle_inbound(db_session, inbound=_inbound(text))
    trigger = out.finalize_trigger_id or ""
    row = (
        await db_session.execute(
            sa.select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == trigger)
        )
    ).scalar_one()
    row.created_at = when
    await db_session.flush()
    return await svc.finalize_buffered_answer(
        db_session, conversation_id=conversation.id, trigger_wa_message_id=trigger
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_yes_sends_intro_and_first_question_as_one_message(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tapping Yes produces a single combined message, not two bubbles."""
    sent = _patch_send_and_score(monkeypatch)
    conversation = await _seed_conversation(
        db_session,
        state=WhatsAppConversationState.AWAITING_INTEREST,
        current_question_index=None,
    )

    yes = InboundMessage(
        from_number=PHONE.removeprefix("+"),
        message_id=f"wamid.in.{uuid.uuid4().hex}",
        type="interactive",
        button_reply_id=WhatsAppButtonId.INTEREST_YES.value,
        button_reply_title="Yes",
    )
    outcome = await svc.handle_inbound(db_session, inbound=yes)

    assert outcome.conversation is not None
    assert outcome.finalize_trigger_id is None  # interest reply isn't buffered
    # Exactly one outbound message — intro folded into question 1.
    assert len(sent) == 1
    assert QUESTIONS_INTRO_MESSAGE in sent[0]
    assert "Question 1 of 3" in sent[0]
    await db_session.refresh(conversation)
    assert conversation.state is WhatsAppConversationState.ASKING_QUESTIONS
    assert conversation.current_question_index == 0


@pytest.mark.asyncio
async def test_rapid_bubbles_coalesce_into_one_answer(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two quick bubbles become one answer; the stale finalize is superseded."""
    sent = _patch_send_and_score(monkeypatch)
    conversation = await _seed_conversation(
        db_session,
        state=WhatsAppConversationState.ASKING_QUESTIONS,
        current_question_index=0,
    )

    # Two bubbles for question 0 — buffered, nothing sent yet.
    out1 = await svc.handle_inbound(db_session, inbound=_inbound("30 days"))
    out2 = await svc.handle_inbound(db_session, inbound=_inbound("maybe sooner"))
    assert out1.finalize_trigger_id is not None
    assert out2.finalize_trigger_id is not None
    assert sent == []  # buffering does not send

    # now() is constant within the test's transaction, so give the two inbound
    # rows distinct created_at to mirror the real two-webhook arrival order.
    rows = await _inbound_rows(db_session, conversation.id)
    assert len(rows) == 2
    base = datetime.now(UTC)
    rows[0].created_at = base
    rows[1].created_at = base + timedelta(seconds=1)
    await db_session.flush()
    first_trigger, last_trigger = rows[0].wa_message_id, rows[1].wa_message_id

    # The earlier bubble's finalize is superseded by the newer arrival.
    superseded = await svc.finalize_buffered_answer(
        db_session, conversation_id=conversation.id, trigger_wa_message_id=first_trigger or ""
    )
    assert superseded is None
    assert sent == []
    await db_session.refresh(conversation)
    assert conversation.current_question_index == 0

    # The latest bubble's finalize coalesces both, scores once, advances once.
    result = await svc.finalize_buffered_answer(
        db_session, conversation_id=conversation.id, trigger_wa_message_id=last_trigger or ""
    )
    assert result is not None
    await db_session.refresh(conversation)
    assert conversation.current_question_index == 1
    assert len(sent) == 1  # exactly one next question
    assert "Question 2 of 3" in sent[0]
    assert len(conversation.answers) == 1
    assert conversation.answers[0]["answer"] == "30 days\nmaybe sooner"


@pytest.mark.asyncio
async def test_only_ai_verified_questions_are_scored(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only questions flagged ``ai_verifies_response`` invoke the Claude scorer.

    Fixed / custom questions store the answer verbatim with null scores; the
    AI background-validation questions are the only ones scored.
    """
    _patch_send_and_score(monkeypatch)
    scored_questions: list[str] = []

    async def _recording_score(*, question: str, answer: str) -> None:
        scored_questions.append(question)
        return None

    monkeypatch.setattr(whatsapp_answer_scorer, "score_answer", _recording_score)

    conversation = await _seed_conversation(
        db_session,
        state=WhatsAppConversationState.ASKING_QUESTIONS,
        current_question_index=0,
    )
    # Override with one fixed (not verified) + one AI (verified) question.
    job = await db_session.get(Job, conversation.job_id)
    assert job is not None
    job.whatsapp_questions = [
        {"id": "q1", "order": 0, "question_en": "Fixed question", "ai_verifies_response": False},
        {"id": "q2", "order": 1, "question_en": "AI question", "ai_verifies_response": True},
    ]
    await db_session.flush()

    base = datetime.now(UTC)
    # Answer the fixed question — scorer must NOT be called, scores stay null.
    await _answer_and_finalize(db_session, conversation, "an answer", when=base)
    assert scored_questions == []
    await db_session.refresh(conversation)
    assert conversation.current_question_index == 1
    assert conversation.answers[0]["relevance_score"] is None
    assert conversation.answers[0]["ai_likelihood_score"] is None

    # Answer the AI question — scorer IS called for it.
    await _answer_and_finalize(
        db_session, conversation, "another answer", when=base + timedelta(seconds=1)
    )
    assert scored_questions == ["AI question"]


@pytest.mark.asyncio
async def test_fixed_eligibility_answer_is_normalized(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fixed eligibility answer is run through the extractor and the normalized
    value lands on the answer entry.

    Salary carries the extracted amount + currency; employment-type folds in the
    *job's* value alongside the accept/decline. Non-extractable questions
    (no ``source_field``) get ``extracted=None``.
    """
    _patch_send_and_score(monkeypatch)

    async def _fake_extract(
        *, source_field: str, question: str, answer: str
    ) -> EligibilityExtraction | None:
        if source_field == "salary":
            return EligibilityExtraction(salary_amount=15_800, salary_currency="AED")
        if source_field == "employment_type":
            return EligibilityExtraction(accepted=True)
        return None

    monkeypatch.setattr(whatsapp_eligibility_extractor, "extract_fixed_answer", _fake_extract)

    conversation = await _seed_conversation(
        db_session,
        state=WhatsAppConversationState.ASKING_QUESTIONS,
        current_question_index=0,
    )
    job = await db_session.get(Job, conversation.job_id)
    assert job is not None
    job.whatsapp_questions = [
        {
            "id": "q1",
            "order": 0,
            "question_en": "Expected salary?",
            "ai_verifies_response": False,
            "source_field": "salary",
        },
        {
            "id": "q2",
            "order": 1,
            "question_en": "Open to permanent?",
            "ai_verifies_response": False,
            "source_field": "employment_type",
        },
        {"id": "q3", "order": 2, "question_en": "Why this role?", "ai_verifies_response": False},
    ]
    await db_session.flush()

    base = datetime.now(UTC)
    # Salary answer → extracted amount + currency.
    await _answer_and_finalize(db_session, conversation, "expecting around 15,800 AED", when=base)
    # Employment-type answer → accept flag + the job's own value folded in.
    await _answer_and_finalize(
        db_session, conversation, "yes, permanent works", when=base + timedelta(seconds=1)
    )
    # A question with no source_field → not extracted.
    await _answer_and_finalize(
        db_session, conversation, "great mission", when=base + timedelta(seconds=2)
    )

    await db_session.refresh(conversation)
    answers = conversation.answers
    assert answers[0]["extracted"] == {"amount": 15_800, "currency": "AED"}
    assert answers[1]["extracted"] == {
        "accepted": True,
        "value": EmploymentType.PERMANENT.value,
    }
    assert answers[2]["extracted"] is None
