"""WhatsApp eligibility extractor (Step 5.5).

Single public coroutine :func:`extract_fixed_answer`. Given a fixed screening
question's ``source_field``, the question text, and the candidate's free-text
reply, it makes one Claude (Haiku — cheap, well-defined extraction, mirrors the
answer scorer) structured-output call and returns an
:class:`~src.schemas.whatsapp_eligibility.EligibilityExtraction` — a small
structured fact (expected salary, notice-period days, visa validity, or an
accept/decline) the score card renders on its Eligibility Questions rows.

On a blank answer or a structured-bind failure it returns ``None`` (the caller
leaves the row un-normalized and the screening flow continues — an extraction
miss must never break the conversation). Caching, retries, and PII-safe logging
come from the Claude wrapper; the question and answer are candidate PII and are
*never* logged — only the ``source_field`` + model.
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
from src.enums.prompts import WHATSAPP_ELIGIBILITY_EXTRACT_SYSTEM_PROMPT
from src.schemas.whatsapp_eligibility import EligibilityExtraction

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE: Final[str] = "whatsapp_eligibility_extract.j2"

# Cache-key tag. Bump the ``v1`` segment when the extraction rubric changes so
# stale cached values are not served.
PROMPT_VERSION: Final[str] = "whatsapp_eligibility_extract:v1"

# The payload is a handful of small scalars — a tight cap keeps cost and latency
# predictable on the inbound finalize path.
MAX_TOKENS: Final[int] = 256

# Built once: literal system turn + the Jinja human template.
_PROMPT: Final = load_chat_prompt(
    system=WHATSAPP_ELIGIBILITY_EXTRACT_SYSTEM_PROMPT, human_template_file=PROMPT_TEMPLATE
)


async def extract_fixed_answer(
    *, source_field: str, question: str, answer: str
) -> EligibilityExtraction | None:
    """Extract one structured fact from a fixed-question answer, or ``None``.

    Args:
        source_field: The fixed question's key (e.g. ``salary``, ``visa``) —
            named to the model so it fills only the relevant field.
        question: The screening question that was asked (candidate-facing text).
        answer: The candidate's free-text reply.

    Returns:
        A validated :class:`EligibilityExtraction`, or ``None`` when the answer
        is blank or the model's response fails to bind to the schema (logged
        PII-safely; the caller treats this as "not extracted" and continues).
    """
    if not answer.strip():
        return None
    try:
        result = await call_claude_structured(
            model=ClaudeModel.HAIKU,
            prompt=_PROMPT,
            variables={"field": source_field, "question": question, "answer": answer},
            schema=EligibilityExtraction,
            max_tokens=MAX_TOKENS,
            prompt_version=PROMPT_VERSION,
        )
    except ClaudeStructuredOutputError:
        logger.warning(
            "whatsapp.eligibility.bind_failed",
            extra={"model": ClaudeModel.HAIKU.value, "source_field": source_field},
        )
        return None

    logger.info(
        "whatsapp.eligibility.extracted",
        extra={"model": ClaudeModel.HAIKU.value, "source_field": source_field},
    )
    return result
