"""Unit tests for ``AuthenticityBand``.

Locks the exact string values because they appear in JSONB breakdowns
and HR-facing UI labels.
"""

from __future__ import annotations

from src.enums.authenticity import AuthenticityBand


def test_authenticity_band_values() -> None:
    assert {e.value for e in AuthenticityBand} == {"authentic", "review", "fabricated"}


def test_authenticity_band_str_enum() -> None:
    # StrEnum so values can be passed straight to JSON serializers.
    assert AuthenticityBand.AUTHENTIC == "authentic"
    assert str(AuthenticityBand.REVIEW) == "review"
