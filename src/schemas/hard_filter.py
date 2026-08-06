"""Pydantic schemas for hard-filter scoring (Step 4.3).

Three shapes, matching the three layers:

* :class:`HardFilterSignalReport` — *one* signal as Claude returns it
  (``score`` 0-100 + ``reasoning``).
* :class:`HardFilterClaudeResponse` — the full Claude payload, one
  field per :class:`~src.enums.hard_filters.HardFilterKey`.
  ``extra='forbid'`` so a stray key invents a validation failure that
  triggers the strict retry.
* :class:`HardFilterScoreResult` — service-level return: the weighted
  total + the per-signal breakdown (score + reasoning + weight). What
  the worker persists onto ``applications.hard_filter_breakdown`` and
  the ``application_scores.breakdown`` audit row.

Wire format and result format are kept separate so the service can do
sanitisation (clamp out-of-range, attach weights) in one well-tested
place without changing the schema Claude has to produce.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from src.enums.hard_filters import HardFilterKey

# Score bounds match the architecture-doc rubric: each signal is rated
# 0..100 by Sonnet. Integer in the wire format (matches the prompt's
# instruction); the service may upcast to float for the weighted total.
SIGNAL_SCORE_MIN: Final[int] = 0
SIGNAL_SCORE_MAX: Final[int] = 100

# Sanity cap on the reasoning string. A scorer that wants to write a
# 2000-word essay is misbehaving. Anything over this gets rejected by
# the schema, triggering the strict retry.
REASONING_MAX_LENGTH: Final[int] = 1000

# The ``evidence`` quote and the optional ``gap`` note are recruiter-facing
# one-liners, not essays — cap them well below the reasoning limit. An
# over-long value fails validation and triggers the strict retry.
EVIDENCE_MAX_LENGTH: Final[int] = 600
GAP_MAX_LENGTH: Final[int] = 600


class HardFilterSignalReport(BaseModel):
    """One signal entry inside Claude's response.

    ``reasoning`` is the recruiter-facing analysis (what the CV Score card
    renders); ``evidence`` is the direct CV quote (or an explicit "not
    required" / "no evidence" note) that grounds the score — the evidence-first
    rule makes the model find it *before* scoring; ``gap`` is an optional note
    on what is missing or weak for this signal.
    """

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=SIGNAL_SCORE_MIN, le=SIGNAL_SCORE_MAX)
    reasoning: str = Field(min_length=1, max_length=REASONING_MAX_LENGTH)
    evidence: str = Field(min_length=1, max_length=EVIDENCE_MAX_LENGTH)
    gap: str | None = Field(default=None, max_length=GAP_MAX_LENGTH)


class HardFilterClaudeResponse(BaseModel):
    """Full Claude response shape for the hard-filter prompt.

    Field names match :class:`HardFilterKey` values verbatim so a model
    that emits an unexpected key (or omits one) fails validation and
    triggers the strict-prompt retry path.
    """

    model_config = ConfigDict(extra="forbid")

    skills: HardFilterSignalReport
    experience: HardFilterSignalReport
    industry: HardFilterSignalReport
    regional: HardFilterSignalReport
    role_seniority: HardFilterSignalReport

    def as_dict(self) -> dict[HardFilterKey, HardFilterSignalReport]:
        """Map keyed by :class:`HardFilterKey` for downstream aggregation."""
        return {
            HardFilterKey.SKILLS: self.skills,
            HardFilterKey.EXPERIENCE: self.experience,
            HardFilterKey.INDUSTRY: self.industry,
            HardFilterKey.REGIONAL: self.regional,
            HardFilterKey.ROLE_SENIORITY: self.role_seniority,
        }


class HardFilterSignalDetail(BaseModel):
    """One signal in the service-level breakdown: score + reasoning + weight.

    Carries the grounding ``evidence`` quote and optional ``gap`` note through
    to the persisted breakdown (``applications.hard_filter_breakdown`` and the
    ``application_scores.breakdown`` audit row) so HR sees the quote behind
    each signal. Both are absent on rows scored before the evidence rollout;
    the read path serves the breakdown as an opaque dict, so those older rows
    keep working unchanged.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    score: float = Field(ge=float(SIGNAL_SCORE_MIN), le=float(SIGNAL_SCORE_MAX))
    reasoning: str
    evidence: str
    gap: str | None = None
    weight: float = Field(gt=0.0, le=100.0)


class HardFilterScoreResult(BaseModel):
    """Service return shape: weighted total + per-signal breakdown.

    ``total`` is the weighted sum (0..100, float) the worker writes
    onto ``applications.hard_filter_score``. ``signals`` is the dict
    that goes into ``applications.hard_filter_breakdown`` and the
    ``application_scores.breakdown`` audit JSONB.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    total: float = Field(ge=float(SIGNAL_SCORE_MIN), le=float(SIGNAL_SCORE_MAX))
    signals: dict[HardFilterKey, HardFilterSignalDetail]
    prompt_version: str
    model_used: str
