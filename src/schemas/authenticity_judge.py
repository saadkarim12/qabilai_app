"""Pydantic schemas for the authenticity LLM judge.

The judge is a single Claude (Sonnet) call scoring every authenticity
signal on a 0-100 scale (100 = authentic / no concern, 0 = strong
fabrication concern). Two wire shapes, one per calibration mode:

* :class:`AuthenticityBaselineResponse` — the five **job-agnostic**
  signals, used when there's no job context (pool-only CV ingest). Feeds
  the candidate-level baseline score.
* :class:`AuthenticityJobAwareResponse` — all six signals (adds
  ``jd_keyword_mirroring``), used when authenticity runs against an
  application. Feeds the application-level job-aware score, and its five
  job-agnostic signals also derive the candidate baseline.

Both carry a ``rationale`` — the recruiter-facing comment shown under the
band label (2-3 neutral, advisory sentences; no scores or signal names; see
the RECRUITER-FACING COMMENT rule in the system prompt). The per-signal
scores/findings stay internal (audit + band math). ``extra='forbid'`` so a
reply that invents keys fails validation and triggers the strict retry in
:mod:`src.services.authenticity.llm_judge`.

Each signal is a :class:`JudgeSignalReport` (``score`` + ``finding``, the
evidence-quoting justification). The derived ``concern`` flag and the
``top_concerns`` list are computed server-side from the scores — never
trusted from the model — so they can't drift from the numbers.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from src.enums.authenticity_signals import SignalKey
from src.schemas.authenticity import SIGNAL_SCORE_MAX, SIGNAL_SCORE_MIN, SignalResult

# Claude is asked to emit integer scores 0..100. Floats would also be
# fine, but integers are easier for the model to keep self-consistent
# inside its reasoning chain ("85 because X" reads more cleanly than
# "85.3 because X").
JUDGE_SCORE_MIN: Final[int] = int(SIGNAL_SCORE_MIN)
JUDGE_SCORE_MAX: Final[int] = int(SIGNAL_SCORE_MAX)

# Sanity cap on the per-signal finding string. A judge that wants to write
# a paragraph per signal is misbehaving; the synthesis belongs in
# ``rationale``. Anything over this is rejected by the schema. The prompt asks
# for far shorter (≤2 sentences); this ceiling has headroom so a slightly
# verbose-but-valid finding still binds on the first attempt rather than
# forcing an expensive strict retry. Skill-dense CVs used to push
# ``timeline_coherence``/``consistency`` past the old 600-char cap; the
# judge's salvage step truncates anything still over this to fit.
JUDGE_FINDING_MAX_LENGTH: Final[int] = 900

# Cap on the synthesis paragraph. Generous headroom for the same reason.
JUDGE_RATIONALE_MAX_LENGTH: Final[int] = 2000


class JudgeSignalReport(BaseModel):
    """One sub-signal entry inside Claude's response."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=JUDGE_SCORE_MIN, le=JUDGE_SCORE_MAX)
    finding: str = Field(min_length=1, max_length=JUDGE_FINDING_MAX_LENGTH)


class AuthenticityBaselineResponse(BaseModel):
    """Five job-agnostic signals — the no-job (pool) calibration.

    ``extra='forbid'`` means a reply that adds ``jd_keyword_mirroring`` (or
    any metadata field) fails validation and triggers the strict retry.
    """

    model_config = ConfigDict(extra="forbid")

    specificity: JudgeSignalReport
    timeline_coherence: JudgeSignalReport
    consistency: JudgeSignalReport
    linguistic_genericity: JudgeSignalReport
    structural_templating: JudgeSignalReport
    rationale: str = Field(min_length=1, max_length=JUDGE_RATIONALE_MAX_LENGTH)

    def signals(self) -> dict[SignalKey, JudgeSignalReport]:
        """Map each signal to its report, keyed by :class:`SignalKey`."""
        return {
            SignalKey.SPECIFICITY: self.specificity,
            SignalKey.TIMELINE_COHERENCE: self.timeline_coherence,
            SignalKey.CONSISTENCY: self.consistency,
            SignalKey.LINGUISTIC_GENERICITY: self.linguistic_genericity,
            SignalKey.STRUCTURAL_TEMPLATING: self.structural_templating,
        }


class AuthenticityJobAwareResponse(AuthenticityBaselineResponse):
    """All six signals — the with-job calibration (adds JD Keyword Mirroring)."""

    jd_keyword_mirroring: JudgeSignalReport

    def signals(self) -> dict[SignalKey, JudgeSignalReport]:
        return {
            **super().signals(),
            SignalKey.JD_KEYWORD_MIRRORING: self.jd_keyword_mirroring,
        }


class JudgeResult(BaseModel):
    """Service-level return shape: per-signal results + the judge's synthesis.

    ``signals`` holds one :class:`SignalResult` per scored signal (five or
    six, depending on calibration), already converted — score clamped,
    ``computed_by=CLAUDE``, ``finding`` + derived ``concern`` set.
    ``rationale`` is ``None`` on a judge-failure fallback. Frozen so the
    aggregator can pass it around without defensive copies.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    signals: dict[SignalKey, SignalResult]
    rationale: str | None = None
