"""Hardcoded WhatsApp screening questions, fixed per job.

Every job gets the *same* deterministic screening questions below, rendered
from these templates with a few ``{placeholder}`` tokens filled from the Job
(``{currency}``, ``{city}``, ``{employment_type}``, ``{work_mode}``). HR no
longer ticks which fields to ask — the set is fixed on the backend. The
question generator (:mod:`src.ai.whatsapp_question_generator`) emits one
:class:`WhatsAppQuestion` per template (``is_ai_generated=False``,
``ai_verifies_response=False``, ``source_field=<key>``) and then appends the
AI-authored ``background_validation`` questions, which are the only ones the
candidate's reply is AI-scored against.

These cover the candidate-facing ``COMMITMENT`` / ``SALARY`` topics (the fixed
visa question is a ``COMMITMENT`` item). Verifying hands-on experience with the
role's listed required + preferred skills is left entirely to the AI
``BACKGROUND_VALIDATION`` questions. Categories stay the three
:class:`QuestionCategory` values; the specific topic lives in ``subcategory``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from src.enums.whatsapp_questions import QuestionCategory
from src.schemas.whatsapp_question import WhatsAppQuestionDraft

if TYPE_CHECKING:
    from src.db.models.job import Job


@dataclass(frozen=True)
class FixedQuestionTemplate:
    """A canned fixed question; text may carry ``{placeholders}``.

    ``key`` is stamped onto the generated question's ``source_field`` so the
    frontend can render it as a locked "Fixed" question (vs an AI or custom one).
    """

    key: str
    category: QuestionCategory
    subcategory: str
    question_en: str
    question_ar: str
    reasoning: str


# The fixed question set, in the order it is asked. Placeholders are filled
# from the Job at generation time. None of these are AI-scored — see
# ``ai_verifies_response`` on the generated questions.
FIXED_QUESTIONS: Final[tuple[FixedQuestionTemplate, ...]] = (
    FixedQuestionTemplate(
        key="commitment",
        category=QuestionCategory.COMMITMENT,
        subcategory="Motivation",
        question_en="What is your main reason for considering a new opportunity right now?",
        question_ar="ما هو السبب الرئيسي الذي يدفعك للتفكير في فرصة عمل جديدة في الوقت الحالي؟",
        reasoning=(
            "Surfaces the candidate's motivation for moving so HR can gauge commitment and fit."
        ),
    ),
    FixedQuestionTemplate(
        key="salary",
        category=QuestionCategory.SALARY,
        subcategory="Salary expectation",
        question_en=(
            "What is your current monthly salary, and what would be your expected "
            "salary for this role? Please share figures in {currency}."
        ),
        question_ar=(
            "ما هو راتبك الشهري الحالي، وما هو الراتب المتوقع لهذا الدور؟ "
            "يرجى ذكر المبالغ بعملة {currency}."
        ),
        reasoning=(
            "Captures current and expected pay so HR can check alignment with the role's band."
        ),
    ),
    FixedQuestionTemplate(
        key="notice_period",
        category=QuestionCategory.COMMITMENT,
        subcategory="Notice period",
        question_en="What is your notice period in days?",
        question_ar="ما هي مدة فترة الإشعار لديك بالأيام؟",
        reasoning="Establishes how soon the candidate can join relative to the role's needs.",
    ),
    FixedQuestionTemplate(
        key="visa",
        category=QuestionCategory.COMMITMENT,
        subcategory="Visa status",
        question_en="What is your current visa or residency status in {city}?",
        question_ar="ما هو وضع التأشيرة أو الإقامة الحالي لديك في {city}؟",
        reasoning="Confirms the candidate's work-authorization status for the role's location.",
    ),
    FixedQuestionTemplate(
        key="employment_type",
        category=QuestionCategory.COMMITMENT,
        subcategory="Employment type",
        question_en="This is a {employment_type} role. Are you open to this type of employment?",
        question_ar="هذا الدور وظيفة {employment_type}. هل أنت منفتح على هذا النوع من التوظيف؟",
        reasoning="Checks the candidate accepts the role's employment type.",
    ),
    FixedQuestionTemplate(
        key="work_mode",
        category=QuestionCategory.COMMITMENT,
        subcategory="Work mode",
        question_en=(
            "This role is {work_mode} in {city}. Are you comfortable with this arrangement?"
        ),
        question_ar="هذا الدور {work_mode} في {city}. هل أنت مرتاح لهذا الترتيب؟",
        reasoning="Checks the candidate is comfortable with the role's work mode and location.",
    ),
)

# Source-field keys the generated fixed questions carry, in canonical order.
FIXED_QUESTION_KEYS: Final[tuple[str, ...]] = tuple(t.key for t in FIXED_QUESTIONS)


def _placeholder_context(job: Job) -> dict[str, str]:
    """Values available to fill ``{placeholder}`` tokens in the templates."""
    return {
        "currency": job.currency,
        "city": job.city,
        "employment_type": job.employment_type.value,
        "work_mode": job.work_mode.value,
    }


def build_fixed_questions(job: Job) -> list[tuple[str, WhatsAppQuestionDraft]]:
    """Render every fixed question for ``job``, with placeholders filled.

    Returns ``(source_field_key, draft)`` pairs in canonical order. The caller
    mints ids/order and stamps provenance.
    """
    context = _placeholder_context(job)
    return [
        (
            template.key,
            WhatsAppQuestionDraft(
                category=template.category,
                subcategory=template.subcategory,
                question_en=template.question_en.format_map(context),
                question_ar=template.question_ar.format_map(context),
                reasoning=template.reasoning,
            ),
        )
        for template in FIXED_QUESTIONS
    ]
