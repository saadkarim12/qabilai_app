"""Pydantic schema for the WhatsApp answer scorer (Step 5.4).

One Claude (Haiku) call rates a single free-text screening answer on two
independent 0-10 sub-scores so HR can triage replies at a glance:

* ``relevance_score`` — how well the answer actually addresses the question
  that was asked (10 = directly, fully on-topic; 0 = irrelevant / empty).
* ``ai_likelihood_score`` — how machine-generated the answer reads (10 =
  almost certainly AI-written; 0 = clearly a human's own words). **Higher is
  the *worse* signal here** — it flags likely AI use, not quality.

``rationale`` is a short HR-facing note explaining the two scores. Like every
Claude wire schema it uses ``extra='forbid'`` so a stray key triggers the
caller's failure path rather than silently passing.

The answer text and the rationale are candidate PII / content and are *never*
logged — only the two integer scores + model appear in log records.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

# Both sub-scores live on a 0-10 integer scale. Integers (not floats) keep the
# model self-consistent inside its reasoning and are trivial for HR to read.
ANSWER_SCORE_MIN: Final[int] = 0
ANSWER_SCORE_MAX: Final[int] = 10

# Sanity cap on the rationale. A one-liner is the intent; anything longer is
# clipped by the schema before it reaches storage.
ANSWER_RATIONALE_MAX_LENGTH: Final[int] = 500


class AnswerScoreResponse(BaseModel):
    """Claude's response shape for scoring one screening answer.

    ``extra='forbid'`` so an invented field fails validation and surfaces as a
    :class:`~src.ai.claude.ClaudeStructuredOutputError` the caller can absorb.
    """

    model_config = ConfigDict(extra="forbid")

    relevance_score: int = Field(
        ge=ANSWER_SCORE_MIN,
        le=ANSWER_SCORE_MAX,
        description="How directly the answer addresses the question (10 = fully on-topic).",
    )
    ai_likelihood_score: int = Field(
        ge=ANSWER_SCORE_MIN,
        le=ANSWER_SCORE_MAX,
        description="How AI-generated the answer reads (10 = almost certainly AI-written).",
    )
    rationale: str = Field(
        min_length=1,
        max_length=ANSWER_RATIONALE_MAX_LENGTH,
        description="One short HR-facing note explaining both scores.",
    )
