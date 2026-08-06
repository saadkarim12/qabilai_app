"""Authenticity aggregation service.

Single public function :func:`aggregate`. It combines the per-signal
:class:`~src.schemas.authenticity.SignalResult` map from the LLM judge
(:mod:`src.services.authenticity.llm_judge`) into a single 0-100
:class:`~src.schemas.authenticity.AuthenticityResult` with band.

The math is a weighted mean normalised by the weights actually present, so
the same :data:`~src.enums.thresholds.AUTHENTICITY_WEIGHTS` dict serves
both scores:

* the **baseline** (five job-agnostic signals) divides by 0.90,
* the **job-aware** score (all six signals) divides by 1.00.

    score = sum(signal.score * WEIGHT[key] for key in signals)
            / sum(WEIGHT[key] for key in signals)
    band  = band_for_score(score)

``top_concerns`` is derived here (lowest-scoring flagged signals first),
never trusted from the model, so it can't drift from the numbers. The
returned ``breakdown`` is the per-signal map indexed by
:class:`~src.enums.authenticity_signals.SignalKey`, each carrying its own
``computed_by`` tag for the persisted breakdown JSON.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Final

from src.enums.authenticity_signals import SignalKey
from src.enums.thresholds import (
    AUTHENTICITY_TOP_CONCERNS_MAX,
    AUTHENTICITY_WEIGHTS,
    band_for_score,
)
from src.schemas.authenticity import (
    SIGNAL_SCORE_MAX,
    SIGNAL_SCORE_MIN,
    AuthenticityResult,
    SignalResult,
)

logger = logging.getLogger(__name__)

# Number of decimals the persisted aggregate score is rounded to. Per-signal
# scores are kept verbatim in the breakdown.
AUTHENTICITY_SCORE_DECIMALS: Final[int] = 2


def _clamp(value: float) -> float:
    """Clamp ``value`` to the schema's 0..100 range (guards float drift)."""
    return max(SIGNAL_SCORE_MIN, min(SIGNAL_SCORE_MAX, value))


def _derive_top_concerns(signals: Mapping[SignalKey, SignalResult]) -> list[str]:
    """Signal-key values flagged as concerns, lowest score first, capped."""
    flagged = sorted(
        (key for key, result in signals.items() if result.concern),
        key=lambda key: signals[key].score,
    )
    return [key.value for key in flagged[:AUTHENTICITY_TOP_CONCERNS_MAX]]


def aggregate(
    signals: Mapping[SignalKey, SignalResult],
    *,
    rationale: str | None = None,
) -> AuthenticityResult:
    """Combine the per-signal map into one :class:`AuthenticityResult`.

    Args:
        signals: One :class:`SignalResult` per scored signal — five
            (baseline) or six (job-aware). Must be non-empty.
        rationale: The judge's synthesis paragraph, if any.

    Returns:
        :class:`AuthenticityResult` with a clamped, rounded score, the
        derived band, the verbatim per-signal breakdown, the derived
        ``top_concerns``, and the ``rationale``.
    """
    if not signals:
        raise ValueError("aggregate() requires at least one signal")

    weight_total = sum(AUTHENTICITY_WEIGHTS[key] for key in signals)
    raw_score = (
        sum(result.score * AUTHENTICITY_WEIGHTS[key] for key, result in signals.items())
        / weight_total
    )
    score = round(_clamp(raw_score), AUTHENTICITY_SCORE_DECIMALS)
    band = band_for_score(score)
    top_concerns = _derive_top_concerns(signals)

    logger.info(
        "authenticity.aggregated",
        extra={
            "score": score,
            "band": band.value,
            "signal_count": len(signals),
            # Per-signal scores only — never any finding text (could reflect
            # bits of CV content).
            "signal_scores": {key.value: result.score for key, result in signals.items()},
            "top_concerns": top_concerns,
        },
    )

    return AuthenticityResult(
        score=score,
        band=band,
        breakdown=dict(signals),
        top_concerns=top_concerns,
        rationale=rationale,
    )
