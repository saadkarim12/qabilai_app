"""Unit tests for tuning thresholds."""

from __future__ import annotations

import math

import pytest

from src.enums.authenticity import AuthenticityBand
from src.enums.authenticity_signals import SignalKey
from src.enums.thresholds import (
    AUTHENTICITY_BAND_THRESHOLDS,
    AUTHENTICITY_WEIGHTS,
    SIMILARITY_REJECTION_THRESHOLD,
    band_for_score,
)


def test_similarity_threshold_is_a_cosine_distance() -> None:
    # Distance on the 0-100 percentage scale similarity scores now use.
    assert 0.0 <= SIMILARITY_REJECTION_THRESHOLD <= 100.0


def test_band_thresholds_match_doc_75_50() -> None:
    """Doc Step 3.7 acceptance: 'Band assignment matches thresholds (75/50)'."""
    assert AUTHENTICITY_BAND_THRESHOLDS[AuthenticityBand.AUTHENTIC] == 75.0
    assert AUTHENTICITY_BAND_THRESHOLDS[AuthenticityBand.REVIEW] == 50.0
    assert AUTHENTICITY_BAND_THRESHOLDS[AuthenticityBand.FABRICATED] == 0.0


def test_band_thresholds_strictly_decreasing() -> None:
    a = AUTHENTICITY_BAND_THRESHOLDS[AuthenticityBand.AUTHENTIC]
    r = AUTHENTICITY_BAND_THRESHOLDS[AuthenticityBand.REVIEW]
    f = AUTHENTICITY_BAND_THRESHOLDS[AuthenticityBand.FABRICATED]
    assert a > r > f >= 0.0
    assert a <= 100.0


def test_band_thresholds_cover_every_band() -> None:
    assert set(AUTHENTICITY_BAND_THRESHOLDS.keys()) == set(AuthenticityBand)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100.0, AuthenticityBand.AUTHENTIC),
        (75.0, AuthenticityBand.AUTHENTIC),
        (74.99, AuthenticityBand.REVIEW),
        (50.0, AuthenticityBand.REVIEW),
        (49.99, AuthenticityBand.FABRICATED),
        (0.0, AuthenticityBand.FABRICATED),
    ],
)
def test_band_for_score(score: float, expected: AuthenticityBand) -> None:
    assert band_for_score(score) is expected


def test_authenticity_weights_cover_every_signal() -> None:
    """Every SignalKey must have a weight or the aggregator under-counts."""
    assert set(AUTHENTICITY_WEIGHTS.keys()) == set(SignalKey)


def test_authenticity_weights_sum_to_one() -> None:
    """The full six-signal weighting sums to 1.0 (job-aware score).

    The baseline (five job-agnostic signals) reuses the same dict; the
    aggregator normalises by the sum of the weights present, so the subset
    divides by 0.90.
    """
    assert math.isclose(sum(AUTHENTICITY_WEIGHTS.values()), 1.0, abs_tol=1e-9)


def test_authenticity_weights_match_doc_ratios() -> None:
    """Pin the exact ratios so accidental retuning is caught."""
    assert AUTHENTICITY_WEIGHTS[SignalKey.SPECIFICITY] == 0.25
    assert AUTHENTICITY_WEIGHTS[SignalKey.TIMELINE_COHERENCE] == 0.20
    assert AUTHENTICITY_WEIGHTS[SignalKey.CONSISTENCY] == 0.20
    assert AUTHENTICITY_WEIGHTS[SignalKey.LINGUISTIC_GENERICITY] == 0.15
    assert AUTHENTICITY_WEIGHTS[SignalKey.STRUCTURAL_TEMPLATING] == 0.10
    assert AUTHENTICITY_WEIGHTS[SignalKey.JD_KEYWORD_MIRRORING] == 0.10
