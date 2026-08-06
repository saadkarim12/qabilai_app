"""Pydantic schemas for authenticity-signal outputs.

A :class:`SignalResult` is the value every signal producer returns —
deterministic Python functions in Step 3.5 and the Claude judge in Step
3.6. Frozen so accidental mutation downstream is a hard error.

``reasons`` is the human-readable "why this score" — what HR sees when
they open a candidate's breakdown. ``details`` is the raw diagnostics
(matched skills, em-dash count, marker density) the engineering side uses
when debugging a surprising score. Both are optional with empty defaults
so callers can keep things terse when there's nothing useful to say.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from src.enums.authenticity import AuthenticityBand
from src.enums.authenticity_signals import SignalComputedBy, SignalKey

# --- Score bounds -----------------------------------------------------------
# Every signal score lives on the same 0..100 scale so the aggregator can
# blend them with weights without per-signal renormalization.

SIGNAL_SCORE_MIN: Final[float] = 0.0
SIGNAL_SCORE_MAX: Final[float] = 100.0


class SignalResult(BaseModel):
    """One authenticity sub-signal's outcome.

    Construction enforces ``0 <= score <= 100`` via Pydantic's ``ge`` / ``le``
    constraints — the computing function is responsible for clamping
    before construction. Frozen (immutable) so the aggregator can pass
    these around without defensive copying.

    ``finding`` is the one-sentence, evidence-quoting justification the LLM
    judge returns for this signal (what HR reads). ``concern`` is the
    derived flag — ``score`` fell below
    :data:`~src.enums.thresholds.AUTHENTICITY_CONCERN_THRESHOLD`. ``reasons``
    mirrors ``finding`` as a list for the existing detail-UI plumbing;
    ``details`` carries raw diagnostics for debugging.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    score: float = Field(ge=SIGNAL_SCORE_MIN, le=SIGNAL_SCORE_MAX)
    computed_by: SignalComputedBy
    finding: str | None = None
    concern: bool = False
    reasons: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class AuthenticityResult(BaseModel):
    """Final authenticity verdict for one CV (aggregator output).

    Persistable as-is: ``model_dump(mode='json')`` produces the exact
    shape that lives in the ``authenticity_breakdown`` (jsonb) column
    alongside ``authenticity_score`` / ``..._band`` scalars — on
    ``candidates`` for the job-agnostic baseline, on ``applications`` for
    the job-aware score. The breakdown dict is keyed by :class:`SignalKey`
    so the JSON is type-stable across writers; downstream readers (the HR
    review UI) can iterate keys without hard-coding strings.

    ``top_concerns`` lists the signal keys (values) the judge flagged as
    concerning, lowest first (internal — not shown to the recruiter);
    ``rationale`` is the recruiter-facing comment (2-3 neutral, advisory
    sentences; no scores or signal names). Both are ``None`` / empty for a
    fallback (judge-failed) result.

    Frozen so passing this object through the application-intake
    service doesn't need defensive copies.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    score: float = Field(ge=SIGNAL_SCORE_MIN, le=SIGNAL_SCORE_MAX)
    band: AuthenticityBand
    breakdown: dict[SignalKey, SignalResult]
    top_concerns: list[str] = Field(default_factory=list)
    rationale: str | None = None
