"""Pydantic schema for the WhatsApp eligibility extractor (Step 5.5).

One Claude (Haiku) call reads a single fixed-question answer and returns a small
structured fact. Every field is optional: the caller names one field to extract
(via the prompt) and the model fills only that field, leaving the rest null. The
finalize path then picks the relevant field(s) for the question's
``source_field`` and stashes a normalized dict on the answer entry.

Like every Claude wire schema it uses ``extra='forbid'`` so a stray key trips
the caller's failure path (treated as "not extracted") rather than passing
silently. The question / answer are candidate PII and are *never* logged — only
the ``source_field`` + model appear in log records.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 3-letter ISO currency codes (AED, SAR, …); a little headroom for stray input.
CURRENCY_MAX_LENGTH: Final[int] = 8
# Sanity caps so a hallucinated figure can't land an absurd value in the UI.
SALARY_AMOUNT_MAX: Final[int] = 100_000_000
NOTICE_PERIOD_DAYS_MAX: Final[int] = 3650  # ~10 years


class EligibilityExtraction(BaseModel):
    """Claude's structured read of one fixed screening answer.

    ``extra='forbid'`` so an invented field fails validation and surfaces as a
    :class:`~src.ai.claude.ClaudeStructuredOutputError` the caller absorbs.
    """

    model_config = ConfigDict(extra="forbid")

    salary_amount: int | None = Field(
        default=None,
        ge=0,
        le=SALARY_AMOUNT_MAX,
        description="Candidate's EXPECTED monthly salary, plain integer (upper end of a range).",
    )
    salary_currency: str | None = Field(
        default=None,
        max_length=CURRENCY_MAX_LENGTH,
        description="Currency code for the expected salary (e.g. AED, SAR). Null if not stated.",
    )
    notice_period_days: int | None = Field(
        default=None,
        description=(
            "How soon the candidate can start, in days (immediate = 0), "
            f"0-{NOTICE_PERIOD_DAYS_MAX}. Null if unclear or implausible."
        ),
    )
    visa_valid: bool | None = Field(
        default=None,
        description="True if the candidate holds valid work authorization for the role's location.",
    )
    accepted: bool | None = Field(
        default=None,
        description="For an employment-type / work-mode question: true if the candidate agrees.",
    )

    @field_validator("notice_period_days")
    @classmethod
    def _drop_implausible_notice(cls, v: int | None) -> int | None:
        """Drop an out-of-range notice period to null instead of failing the bind.

        The cap is a UI-sanity guard, not a data contract: enforcing it as a hard
        ``le`` constraint made a single implausible figure (e.g. "10000 days")
        raise and discard the *whole* extraction. Clamping to null keeps the bind
        succeeding and every other field intact; an absurd value is simply not
        captured (never clamped to the cap, which would fabricate a real-looking
        notice period).
        """
        if v is None or not 0 <= v <= NOTICE_PERIOD_DAYS_MAX:
            return None
        return v
