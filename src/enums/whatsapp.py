"""WhatsApp screening-conversation constants (Step 5.3).

The interest-prompt button ids are a **closed set we route on** — Meta echoes
the tapped button's id back in the inbound webhook (``button_reply.id``), and
the conversation router branches on it — so they live here, never inlined.

The greeting / question / closing copy is held as named templates (not
scattered string literals) so wording changes are one edit. ``*…*`` renders
bold in WhatsApp.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class WhatsAppButtonId(StrEnum):
    """Stable ids for the "are you interested?" quick-reply buttons."""

    INTEREST_YES = "interest_yes"
    INTEREST_NO = "interest_no"


class WhatsAppConversationState(StrEnum):
    """Lifecycle of one application's screening conversation.

    A single open conversation walks ``AWAITING_INTEREST`` → (Yes)
    ``ASKING_QUESTIONS`` → ``COMPLETED``, or short-circuits to ``DECLINED``
    on a No. ``COMPLETED`` / ``DECLINED`` are terminal — later inbound
    messages are recorded but no longer drive the flow.
    """

    AWAITING_INTEREST = "awaiting_interest"
    ASKING_QUESTIONS = "asking_questions"
    COMPLETED = "completed"
    DECLINED = "declined"


class WhatsAppDirection(StrEnum):
    """Whether a persisted message was sent by us or received from the candidate."""

    OUTBOUND = "outbound"
    INBOUND = "inbound"


class WhatsAppMessageType(StrEnum):
    """``whatsapp_messages.message_type`` — what kind of payload the row holds.

    Outbound rows are tagged at send; inbound rows carry the type Meta
    reported (text / interactive button tap / template button tap).
    """

    TEXT = "text"
    INTERACTIVE_BUTTONS = "interactive_buttons"
    TEMPLATE_BUTTONS = "template_buttons"
    BUTTON_REPLY = "button_reply"


# Visible button labels (Cloud API caps titles at 20 chars).
BUTTON_TITLE_YES: Final[str] = "Yes"
BUTTON_TITLE_NO: Final[str] = "No"

# Which localized field of a stored ``WhatsAppQuestion`` we send. English for
# now; an Arabic toggle (``question_ar``) is a later, per-candidate-language
# enhancement.
QUESTION_TEXT_FIELD: Final[str] = "question_en"

# Opening screening message. Placeholders are filled from the candidate / job:
# ``candidate_name`` (Candidate.full_name), ``job_title`` (Job.title),
# ``company`` (Job.hiring_company).
SCREENING_GREETING_TEMPLATE: Final[str] = (
    "Hi {candidate_name}, I'm the Qabil.ai screening assistant for *{company}*. "
    "We received your application for the *{job_title}* role and have a few "
    "quick questions. Ready to start?"
)

# Re-opening message when HR reactivates a previously-closed conversation
# (e.g. the candidate had declined, or screening ended, and HR wants to talk
# again). Deliberately worded differently from the first-contact greeting so
# the candidate understands this is a fresh outreach, not a duplicate. Same
# placeholders as ``SCREENING_GREETING_TEMPLATE``.
SCREENING_REACTIVATED_GREETING_TEMPLATE: Final[str] = (
    "Hi {candidate_name}! 👋\n\n"
    "We're reaching out again about the *{job_title}* role at *{company}*.\n\n"
    "Would you like to continue with your application? We'd be glad to have you."
)

# First-contact greeting for a candidate HR *sourced from the talent pool*
# (we already have their profile — they didn't just apply). Worded as a
# proactive outreach rather than "thanks for applying". Same placeholders.
SCREENING_TALENT_POOL_GREETING_TEMPLATE: Final[str] = (
    "Hi {candidate_name}, *{company}* has a new opening for the *{job_title}* "
    "role and your profile looks like a strong fit. I'd like to ask a few quick "
    "questions to see if this matches what you're looking for. Shall we proceed?"
)

# Sent once interest is confirmed, before the first question.
QUESTIONS_INTRO_MESSAGE: Final[str] = (
    "Great! 🎉 I have a few quick questions about your application. "
    "Please reply to each one and we'll go through them together."
)

# One screening question. ``number``/``total`` give the candidate a sense of
# progress; ``question`` is the localized question text.
QUESTION_PROMPT_TEMPLATE: Final[str] = "*Question {number} of {total}*\n{question}"

# Closing sent after the final question is answered.
SCREENING_COMPLETED_MESSAGE: Final[str] = (
    "That's everything — thank you for your answers! 🙏\n\n"
    "Our team will review your responses and get back to you soon."
)

# Closing sent when the candidate taps No on the interest prompt.
SCREENING_DECLINED_MESSAGE: Final[str] = (
    "Thanks for letting us know, and no problem at all. "
    "We've closed your application for this role. Wishing you all the best! 🙏"
)

# Gentle nudge when we expected a Yes/No tap but got something else.
INTEREST_REPROMPT_MESSAGE: Final[str] = "Please tap *Yes* or *No* above so we can continue. 🙂"

# Human-readable reason stamped on the audit row / application when a
# candidate declines on WhatsApp. HR sees this verbatim on the board.
DECLINE_REASON: Final[str] = "Candidate responded on WhatsApp that they are no longer interested."
