"""Unit tests for the deterministic similarity-breakdown helpers."""

from __future__ import annotations

import pytest

from src.enums.thresholds import SIMILARITY_REJECTION_THRESHOLD
from src.services.scoring import similarity_breakdown


def test_pass_threshold_mirrors_distance_threshold() -> None:
    """The similarity-pass threshold must equal ``100 - distance threshold``
    so the auto-rejector and the breakdown report identical cutoffs."""
    assert similarity_breakdown.SIMILARITY_PASS_THRESHOLD == pytest.approx(
        100.0 - SIMILARITY_REJECTION_THRESHOLD
    )


def test_normalize_strips_dot_js_and_punctuation() -> None:
    """React.js / Node.js / 'REST APIs' normalise to a stable form so the
    JD form ('React.js') matches the typical CV form ('React')."""
    assert similarity_breakdown._normalize("React.js") == "react"
    assert similarity_breakdown._normalize("Node.js") == "node"
    assert similarity_breakdown._normalize(" REST APIs ") == "restapis"
    assert similarity_breakdown._normalize("CI/CD") == "cicd"
    assert similarity_breakdown._normalize("JavaScript") == "javascript"
    # ".js" suffix only — "java" must NOT collapse to anything that
    # collides with "javascript".
    assert similarity_breakdown._normalize("Java") == "java"
    assert similarity_breakdown._normalize("Java") != similarity_breakdown._normalize("JavaScript")


def test_compute_breakdown_partitions_required_and_preferred() -> None:
    """Matched/missing partitioning uses the JD spelling, not the CV one,
    so HR sees the requirement labels it set."""
    out = similarity_breakdown.compute_similarity_breakdown(
        similarity_score=46.54,
        required_skills=["JavaScript", "React.js", "Node.js", "Git"],
        preferred_skills=["Docker", "AWS"],
        cv_skills=["JavaScript", "React", "Git", "HTML"],
    )
    assert out["matched_required_skills"] == ["JavaScript", "React.js", "Git"]
    assert out["missing_required_skills"] == ["Node.js"]
    assert out["matched_preferred_skills"] == []
    assert out["missing_preferred_skills"] == ["Docker", "AWS"]
    assert out["required_skills_total"] == 4
    assert out["required_skills_matched"] == 3


def test_compute_breakdown_threshold_and_margin() -> None:
    out = similarity_breakdown.compute_similarity_breakdown(
        similarity_score=36.54,
        required_skills=[],
        preferred_skills=[],
        cv_skills=[],
    )
    assert out["threshold_similarity"] == pytest.approx(40.0)
    assert out["distance"] == pytest.approx(63.46)
    assert out["passes_threshold"] is False
    # margin == similarity - threshold (negative when below)
    assert out["margin"] == pytest.approx(36.54 - 40.0)


def test_compute_breakdown_above_threshold_passes() -> None:
    out = similarity_breakdown.compute_similarity_breakdown(
        similarity_score=75.0,
        required_skills=["JavaScript"],
        preferred_skills=[],
        cv_skills=["JavaScript"],
    )
    assert out["passes_threshold"] is True
    assert out["margin"] == pytest.approx(35.0)


def test_compute_breakdown_handles_empty_jd_skills() -> None:
    """A job with no parsed required/preferred skills (e.g. parsed_jd not
    yet populated) still produces a valid breakdown — just empty
    matched/missing arrays and a 0 total."""
    out = similarity_breakdown.compute_similarity_breakdown(
        similarity_score=50.0,
        required_skills=[],
        preferred_skills=[],
        cv_skills=["Python", "Java"],
    )
    assert out["required_skills_total"] == 0
    assert out["required_skills_matched"] == 0
    assert out["matched_required_skills"] == []
    assert out["missing_required_skills"] == []


def test_compute_breakdown_filters_non_string_cv_skills() -> None:
    """A malformed parsed_profile (skills array with non-string entries)
    must not crash the breakdown computation."""
    out = similarity_breakdown.compute_similarity_breakdown(
        similarity_score=50.0,
        required_skills=["JavaScript"],
        preferred_skills=[],
        cv_skills=["JavaScript", None, 42, "Python"],  # type: ignore[list-item]
    )
    assert out["matched_required_skills"] == ["JavaScript"]


def test_derive_rejection_reason_when_rejected() -> None:
    breakdown_dict = {
        "threshold_similarity": 40.0,
        "distance": 63.46,
        "required_skills_matched": 3,
        "required_skills_total": 12,
    }
    reason = similarity_breakdown.derive_rejection_reason(
        similarity_breakdown=breakdown_dict, status_is_rejected=True
    )
    assert reason == "similarity 36.5 below 40 threshold; 3/12 required skills matched"


def test_derive_rejection_reason_skips_skills_clause_when_no_required() -> None:
    """A job with zero required skills should omit the 'X/Y matched'
    clause rather than print '0/0'."""
    breakdown_dict = {
        "threshold_similarity": 40.0,
        "distance": 63.46,
        "required_skills_matched": 0,
        "required_skills_total": 0,
    }
    reason = similarity_breakdown.derive_rejection_reason(
        similarity_breakdown=breakdown_dict, status_is_rejected=True
    )
    assert reason == "similarity 36.5 below 40 threshold"


def test_derive_rejection_reason_returns_none_when_not_rejected() -> None:
    breakdown_dict = {
        "threshold_similarity": 40.0,
        "distance": 63.46,
        "required_skills_matched": 3,
        "required_skills_total": 12,
    }
    assert (
        similarity_breakdown.derive_rejection_reason(
            similarity_breakdown=breakdown_dict, status_is_rejected=False
        )
        is None
    )


def test_derive_rejection_reason_returns_none_for_legacy_empty_breakdown() -> None:
    """Pre-rollout score rows have ``breakdown == {}``. The detail
    endpoint must degrade to ``None`` rather than crash or invent a
    reason."""
    assert (
        similarity_breakdown.derive_rejection_reason(
            similarity_breakdown={}, status_is_rejected=True
        )
        is None
    )
    assert (
        similarity_breakdown.derive_rejection_reason(
            similarity_breakdown=None, status_is_rejected=True
        )
        is None
    )


def test_derive_rejection_reason_returns_none_for_missing_keys() -> None:
    """If a breakdown dict is present but malformed (missing the
    distance/threshold keys), don't fabricate values."""
    assert (
        similarity_breakdown.derive_rejection_reason(
            similarity_breakdown={"some_other_key": 1}, status_is_rejected=True
        )
        is None
    )
