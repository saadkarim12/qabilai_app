"""Unit tests for the authenticity aggregator.

Covers:

* Weights sum to 1.0 (six-signal, job-aware).
* Band assignment matches thresholds (75/50).
* Breakdown JSON includes ``computed_by`` per signal.
* Baseline (five job-agnostic signals) vs job-aware (six) normalisation.
* ``top_concerns`` derivation + ``rationale`` passthrough.
* Score clamping / rounding / immutability.
"""

from __future__ import annotations

import math

import pytest

from src.enums.authenticity import AuthenticityBand
from src.enums.authenticity_signals import (
    BASELINE_SIGNAL_KEYS,
    SignalComputedBy,
    SignalKey,
)
from src.enums.thresholds import AUTHENTICITY_WEIGHTS
from src.schemas.authenticity import AuthenticityResult, SignalResult
from src.services.authenticity.aggregator import (
    AUTHENTICITY_SCORE_DECIMALS,
    aggregate,
)

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _signal(score: float, *, concern: bool | None = None) -> SignalResult:
    """A Claude-judged signal; ``concern`` defaults to the score<60 rule."""
    return SignalResult(
        score=score,
        computed_by=SignalComputedBy.CLAUDE,
        concern=(score < 60.0) if concern is None else concern,
    )


def _all_signals(score: float, *, job_aware: bool = True) -> dict[SignalKey, SignalResult]:
    keys = tuple(SignalKey) if job_aware else BASELINE_SIGNAL_KEYS
    return {key: _signal(score) for key in keys}


def _aggregate_at(score: float, *, job_aware: bool = True) -> AuthenticityResult:
    return aggregate(_all_signals(score, job_aware=job_aware))


# ---------------------------------------------------------------------------
# Weights + bands
# ---------------------------------------------------------------------------


def test_weights_sum_to_one() -> None:
    assert math.isclose(sum(AUTHENTICITY_WEIGHTS.values()), 1.0, abs_tol=1e-9)


def test_breakdown_includes_computed_by_per_signal() -> None:
    result = aggregate(_all_signals(80.0))
    breakdown = result.model_dump(mode="json")["breakdown"]
    for key in SignalKey:
        assert breakdown[key.value]["computed_by"] == "claude"


@pytest.mark.parametrize(
    ("score", "expected_band"),
    [
        (100.0, AuthenticityBand.AUTHENTIC),
        (75.0, AuthenticityBand.AUTHENTIC),
        (74.99, AuthenticityBand.REVIEW),
        (50.0, AuthenticityBand.REVIEW),
        (49.99, AuthenticityBand.FABRICATED),
        (0.0, AuthenticityBand.FABRICATED),
    ],
)
def test_band_assignment_matches_thresholds(score: float, expected_band: AuthenticityBand) -> None:
    assert _aggregate_at(score).band is expected_band


# ---------------------------------------------------------------------------
# Baseline vs job-aware normalisation
# ---------------------------------------------------------------------------


def test_uniform_score_preserved_job_aware() -> None:
    for s in (12.3, 47.5, 73.7, 88.0):
        assert _aggregate_at(s, job_aware=True).score == round(s, AUTHENTICITY_SCORE_DECIMALS)


def test_uniform_score_preserved_baseline() -> None:
    """The five-signal baseline normalises by 0.90, so a uniform score is
    still preserved (the doc's original /90 ratios)."""
    for s in (12.3, 47.5, 73.7, 88.0):
        result = _aggregate_at(s, job_aware=False)
        assert result.score == round(s, AUTHENTICITY_SCORE_DECIMALS)
        assert set(result.breakdown.keys()) == set(BASELINE_SIGNAL_KEYS)
        assert SignalKey.JD_KEYWORD_MIRRORING not in result.breakdown


def test_baseline_weighted_ratios_match_published() -> None:
    """Distinct per-signal scores → aggregate equals the weight-normalised
    mean over just the five baseline signals (divide by their weight sum)."""
    scores = {
        SignalKey.SPECIFICITY: 90.0,
        SignalKey.TIMELINE_COHERENCE: 80.0,
        SignalKey.CONSISTENCY: 70.0,
        SignalKey.LINGUISTIC_GENERICITY: 60.0,
        SignalKey.STRUCTURAL_TEMPLATING: 50.0,
    }
    result = aggregate({key: _signal(s) for key, s in scores.items()})
    weight_total = sum(AUTHENTICITY_WEIGHTS[key] for key in scores)
    expected = sum(scores[key] * AUTHENTICITY_WEIGHTS[key] for key in scores) / weight_total
    assert result.score == pytest.approx(round(expected, AUTHENTICITY_SCORE_DECIMALS), abs=0.01)


def test_specificity_outweighs_structural_templating() -> None:
    signals = _all_signals(0.0)
    only_spec = dict(signals, **{SignalKey.SPECIFICITY: _signal(100.0)})
    only_struct = dict(signals, **{SignalKey.STRUCTURAL_TEMPLATING: _signal(100.0)})
    assert aggregate(only_spec).score > aggregate(only_struct).score


# ---------------------------------------------------------------------------
# top_concerns + rationale
# ---------------------------------------------------------------------------


def test_top_concerns_lowest_first_capped_at_three() -> None:
    scores = {
        SignalKey.SPECIFICITY: 90.0,  # not a concern
        SignalKey.TIMELINE_COHERENCE: 55.0,  # concern
        SignalKey.CONSISTENCY: 20.0,  # concern (lowest)
        SignalKey.LINGUISTIC_GENERICITY: 40.0,  # concern
        SignalKey.STRUCTURAL_TEMPLATING: 50.0,  # concern
        SignalKey.JD_KEYWORD_MIRRORING: 95.0,  # not a concern
    }
    result = aggregate({key: _signal(s) for key, s in scores.items()})
    # Four concerns, capped at 3, lowest score first.
    assert result.top_concerns == [
        SignalKey.CONSISTENCY.value,
        SignalKey.LINGUISTIC_GENERICITY.value,
        SignalKey.STRUCTURAL_TEMPLATING.value,
    ]


def test_no_concerns_when_all_high() -> None:
    assert _aggregate_at(90.0).top_concerns == []


def test_rationale_passthrough() -> None:
    result = aggregate(_all_signals(80.0), rationale="Looks solid overall.")
    assert result.rationale == "Looks solid overall."
    assert _aggregate_at(80.0).rationale is None


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


def test_empty_signals_rejected() -> None:
    with pytest.raises(ValueError):
        aggregate({})


def test_result_is_frozen() -> None:
    from pydantic import ValidationError

    result = _aggregate_at(80.0)
    with pytest.raises(ValidationError):
        result.score = 0.0  # type: ignore[misc]


def test_score_is_rounded_to_two_decimals() -> None:
    result = _aggregate_at(33.333333)
    assert result.score == round(33.333333, AUTHENTICITY_SCORE_DECIMALS)
    for key in result.breakdown:
        assert result.breakdown[key].score == 33.333333


def test_model_dump_json_uses_signal_key_string_values() -> None:
    dumped = _aggregate_at(70.0).model_dump(mode="json")
    for key in SignalKey:
        assert key.value in dumped["breakdown"]
    assert dumped["band"] == AuthenticityBand.REVIEW.value
