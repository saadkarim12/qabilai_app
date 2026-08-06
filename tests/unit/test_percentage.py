"""Unit tests for the percentage-formatting helpers used in API responses."""

from __future__ import annotations

from src.utils.percentage import (
    format_percent,
    format_percent_optional,
    percentify_breakdown,
    to_percent,
)


def test_format_percent_trims_trailing_zeros() -> None:
    assert format_percent(82.0) == "82%"
    assert format_percent(35.0) == "35%"
    assert format_percent(0.0) == "0%"


def test_format_percent_keeps_significant_decimals() -> None:
    assert format_percent(67.5) == "67.5%"
    assert format_percent(69.33) == "69.33%"


def test_format_percent_rounds_to_two_decimals() -> None:
    assert format_percent(12.3456) == "12.35%"
    assert format_percent(82.999) == "83%"


def test_format_percent_optional_passes_none_through() -> None:
    assert format_percent_optional(None) is None
    assert format_percent_optional(71.0) == "71%"


def test_to_percent_is_idempotent_and_type_preserving() -> None:
    # Numbers are formatted; already-formatted strings / None pass through.
    assert to_percent(82.0) == "82%"
    assert to_percent("82%") == "82%"
    assert to_percent(None) is None
    # bool is a subclass of int but must NOT be treated as a number.
    assert to_percent(True) is True


def test_percentify_breakdown_formats_score_and_weight_leaves() -> None:
    """Hard-filter shape: per-signal score + weight become percent strings;
    reasoning and other keys are left untouched."""
    breakdown = {
        "skills": {"score": 80.0, "reasoning": "strong match", "weight": 35.0},
        "industry": {"score": 60.0, "reasoning": "adjacent", "weight": 10.0},
    }
    out = percentify_breakdown(breakdown)
    assert out["skills"] == {"score": "80%", "reasoning": "strong match", "weight": "35%"}
    assert out["industry"]["weight"] == "10%"
    # Source dict is not mutated.
    assert breakdown["skills"]["score"] == 80.0


def test_percentify_breakdown_leaves_non_score_weight_keys() -> None:
    """Similarity-breakdown diagnostic keys (distance/threshold/margin) and a
    boolean flag are not score/weight, so they stay as-is."""
    breakdown = {
        "threshold_similarity": 40.0,
        "distance": 63.46,
        "margin": -3.46,
        "passes_threshold": False,
        "matched_required_skills": ["Python", "SQL"],
    }
    out = percentify_breakdown(breakdown)
    assert out == breakdown


def test_percentify_breakdown_recurses_into_lists() -> None:
    out = percentify_breakdown({"signals": [{"score": 50.0}, {"score": 90.0}]})
    assert out["signals"] == [{"score": "50%"}, {"score": "90%"}]
