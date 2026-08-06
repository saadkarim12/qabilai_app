"""WhatsApp screening-answer scorer (Step 5.4).

Single public coroutine :func:`score_answer`. Given the question that was asked
and the candidate's free-text reply, it makes one Claude (Haiku — cheap,
well-defined rubric, mirrors the authenticity judge) structured-output call and
returns two independent 0-10 sub-scores plus a short HR-facing rationale:

* ``relevance_score`` — how well the answer addresses the question,
* ``ai_likelihood_score`` — how AI-generated the answer reads (higher = more
  likely AI).

On a structured-bind failure it returns ``None`` (the caller stores nulls and
keeps the screening flow moving — a scoring miss must never break the
conversation). Caching, retries, and PII-safe logging come from the Claude
wrapper; the question, answer, and rationale are content/PII and are *never*
logged — only the two scores + model.
"""

from __future__ import annotations

import logging
from typing import Final

from src.ai.claude import (
    ClaudeStructuredOutputError,
    call_claude_structured,
    load_chat_prompt,
)
from src.enums.claude_models import ClaudeModel
from src.enums.prompts import WHATSAPP_ANSWER_SCORE_SYSTEM_PROMPT
from src.schemas.whatsapp_answer_score import AnswerScoreResponse

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE: Final[str] = "whatsapp_answer_score.j2"

# Cache-key tag. Bump the ``v1`` segment when the rubric's semantics change so
# stale cached scores are not served.
PROMPT_VERSION: Final[str] = "whatsapp_answer_score:v1"

# The payload is two small integers + one short sentence — a tight cap keeps
# cost and latency predictable on the inbound hot path.
MAX_TOKENS: Final[int] = 512

# Built once: literal system turn + the Jinja human template.
_PROMPT: Final = load_chat_prompt(
    system=WHATSAPP_ANSWER_SCORE_SYSTEM_PROMPT, human_template_file=PROMPT_TEMPLATE
)


async def score_answer(*, question: str, answer: str) -> AnswerScoreResponse | None:
    """Score one screening answer, or ``None`` if Claude can't bind the schema.

    Args:
        question: The screening question that was asked (candidate-facing text).
        answer: The candidate's free-text reply.

    Returns:
        A validated :class:`AnswerScoreResponse`, or ``None`` when the model's
        response fails to bind to the schema (logged PII-safely; the caller
        treats this as "unscored" and continues).
    """
    try:
        result = await call_claude_structured(
            model=ClaudeModel.HAIKU,
            prompt=_PROMPT,
            variables={"question": question, "answer": answer},
            schema=AnswerScoreResponse,
            max_tokens=MAX_TOKENS,
            prompt_version=PROMPT_VERSION,
        )
    except ClaudeStructuredOutputError:
        logger.warning(
            "whatsapp.answer_score.bind_failed",
            extra={"model": ClaudeModel.HAIKU.value, "prompt_version": PROMPT_VERSION},
        )
        return None

    logger.info(
        "whatsapp.answer_score.scored",
        extra={
            "model": ClaudeModel.HAIKU.value,
            "relevance_score": result.relevance_score,
            "ai_likelihood_score": result.ai_likelihood_score,
        },
    )
    return result
