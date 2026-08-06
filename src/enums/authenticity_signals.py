"""Enumerations for authenticity-signal results.

The authenticity score is the weighted combination of five sub-signals
(Step 3.7's aggregator). Two are computed in pure Python (Step 3.5), the
other three by a single Claude call (Step 3.6). Both producers return the
same :class:`~src.schemas.authenticity.SignalResult` shape so the
aggregator can stitch them together without caring which side wrote them.

:class:`SignalComputedBy` is the marker that gets stamped onto every
result and ends up in the breakdown JSON (see
``candidates.authenticity_breakdown``) so a human reading the row can tell
which signals were heuristic vs. LLM-judged.
"""

from __future__ import annotations

from enum import StrEnum


class SignalComputedBy(StrEnum):
    """Which sub-system produced a :class:`SignalResult`.

    The aggregator (Step 3.7) writes this through verbatim onto the
    persisted breakdown — see the doc's acceptance criterion "Breakdown
    JSON includes ``computed_by`` per signal".
    """

    DETERMINISTIC = "deterministic"
    CLAUDE = "claude"


class SignalKey(StrEnum):
    """The authenticity sub-signals the aggregator combines.

    Values are the JSON keys that show up inside
    ``candidates.authenticity_breakdown`` / ``applications.authenticity_breakdown``
    — keep them stable across schema migrations or rewrite the persisted rows.
    The aggregator's weights live in
    :data:`src.enums.thresholds.AUTHENTICITY_WEIGHTS`, keyed by this enum.

    All six signals are LLM-judged by a single Claude call (Sonnet). The
    first five are **job-agnostic** — they depend only on the CV — and make
    up the candidate-level *baseline* score. :attr:`JD_KEYWORD_MIRRORING`
    additionally needs the job context (title + required skills), so it is
    only scored when authenticity runs against an application; the
    application-level *job-aware* score blends all six. :data:`BASELINE_SIGNAL_KEYS`
    names the job-agnostic subset. Every result is stamped
    ``computed_by=CLAUDE`` via :class:`SignalComputedBy`.
    """

    SPECIFICITY = "specificity"
    TIMELINE_COHERENCE = "timeline_coherence"
    CONSISTENCY = "consistency"
    LINGUISTIC_GENERICITY = "linguistic_genericity"
    STRUCTURAL_TEMPLATING = "structural_templating"
    JD_KEYWORD_MIRRORING = "jd_keyword_mirroring"


# The job-agnostic subset (order preserved) that forms the candidate-level
# baseline score. Everything except :attr:`SignalKey.JD_KEYWORD_MIRRORING`,
# which needs a job to score against.
BASELINE_SIGNAL_KEYS: tuple[SignalKey, ...] = (
    SignalKey.SPECIFICITY,
    SignalKey.TIMELINE_COHERENCE,
    SignalKey.CONSISTENCY,
    SignalKey.LINGUISTIC_GENERICITY,
    SignalKey.STRUCTURAL_TEMPLATING,
)
