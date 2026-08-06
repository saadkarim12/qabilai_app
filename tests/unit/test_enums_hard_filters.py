"""Unit tests for the hard-filter scoring constants."""

from __future__ import annotations

import math

from src.enums.hard_filters import (
    HARD_FILTER_RUBRICS,
    HARD_FILTER_WEIGHTS,
    HardFilterKey,
)


def test_hard_filter_key_values() -> None:
    assert {e.value for e in HardFilterKey} == {
        "skills",
        "experience",
        "industry",
        "regional",
        "role_seniority",
    }


def test_weights_cover_every_key() -> None:
    assert set(HARD_FILTER_WEIGHTS.keys()) == set(HardFilterKey)


def test_weights_sum_to_one_hundred() -> None:
    # Weights are percentages out of 100.
    assert math.isclose(sum(HARD_FILTER_WEIGHTS.values()), 100.0, abs_tol=1e-9)


def test_weights_pinned_by_architecture_doc() -> None:
    # The architecture doc fixes these exact splits (percentages out of
    # 100); changing one is a product decision, not a refactor.
    assert HARD_FILTER_WEIGHTS[HardFilterKey.SKILLS] == 35.0
    assert HARD_FILTER_WEIGHTS[HardFilterKey.EXPERIENCE] == 35.0
    assert HARD_FILTER_WEIGHTS[HardFilterKey.INDUSTRY] == 10.0
    assert HARD_FILTER_WEIGHTS[HardFilterKey.REGIONAL] == 10.0
    assert HARD_FILTER_WEIGHTS[HardFilterKey.ROLE_SENIORITY] == 10.0


def test_rubrics_cover_every_key() -> None:
    assert set(HARD_FILTER_RUBRICS.keys()) == set(HardFilterKey)
    for key, rubric in HARD_FILTER_RUBRICS.items():
        assert rubric.strip(), f"empty rubric for {key}"
