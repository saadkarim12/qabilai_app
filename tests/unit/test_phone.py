"""Unit tests for ``src.utils.phone``.

The validator is country-agnostic: every accepted input must be in
international format (``+<country_code><number>``). National formats
are rejected because we cannot reliably infer the country from a bare
national number.
"""

from __future__ import annotations

import pytest

from src.utils.phone import InvalidPhoneNumberError, normalize_phone

# --- Valid inputs (every accepted number carries an explicit + prefix) ----


def test_uae_e164_passes_through() -> None:
    assert normalize_phone("+971501234567") == "+971501234567"


def test_pakistan_e164_passes_through() -> None:
    assert normalize_phone("+923001234567") == "+923001234567"


def test_uk_e164_passes_through() -> None:
    assert normalize_phone("+447911123456") == "+447911123456"


def test_us_e164_passes_through() -> None:
    assert normalize_phone("+14155552671") == "+14155552671"


def test_india_e164_passes_through() -> None:
    assert normalize_phone("+919876543210") == "+919876543210"


def test_formatting_spaces_and_dashes_are_stripped() -> None:
    """Spaces / dashes / parens between digits are normalised away."""
    assert normalize_phone("+971 50-123 4567") == "+971501234567"
    assert normalize_phone("+92 (300) 123-4567") == "+923001234567"


def test_possible_but_unassigned_number_is_accepted() -> None:
    """Possibility, not strict validity: a plausible-shape number with an
    unassigned (e.g. fictional ``555``) area code is accepted, so demo /
    mock CVs go through. See the module docstring."""
    assert normalize_phone("+1 (555) 014-7382") == "+15550147382"


def test_leading_and_trailing_whitespace_tolerated() -> None:
    assert normalize_phone("  +923001234567  ") == "+923001234567"


# --- Invalid inputs --------------------------------------------------------


def test_empty_string_raises() -> None:
    with pytest.raises(InvalidPhoneNumberError, match="empty"):
        normalize_phone("")


def test_whitespace_only_raises() -> None:
    with pytest.raises(InvalidPhoneNumberError, match="empty"):
        normalize_phone("   ")


def test_national_format_without_plus_raises() -> None:
    """National-format submissions are rejected — without a + prefix the
    country is ambiguous and we refuse to guess."""
    with pytest.raises(InvalidPhoneNumberError, match=r"must start with \+"):
        normalize_phone("0501234567")
    with pytest.raises(InvalidPhoneNumberError, match=r"must start with \+"):
        normalize_phone("03001234567")
    with pytest.raises(InvalidPhoneNumberError, match=r"must start with \+"):
        normalize_phone("9876543210")


def test_default_region_normalizes_national_number() -> None:
    """With a default_region, a bare national number is interpreted in that
    region and normalized to E.164 (single-market opt-in)."""
    assert normalize_phone("03409297758", default_region="PK") == "+923409297758"
    assert normalize_phone("0501234567", default_region="AE") == "+971501234567"


def test_default_region_ignored_for_international_number() -> None:
    """An explicit + country code wins over the region hint."""
    assert normalize_phone("+971501234567", default_region="PK") == "+971501234567"


def test_default_region_still_rejects_impossible_national_number() -> None:
    with pytest.raises(InvalidPhoneNumberError):
        normalize_phone("12", default_region="PK")


def test_letters_only_raises() -> None:
    with pytest.raises(InvalidPhoneNumberError):
        normalize_phone("abc")


def test_letters_with_plus_raises() -> None:
    with pytest.raises(InvalidPhoneNumberError):
        normalize_phone("+abc")


def test_too_short_with_plus_raises() -> None:
    """A 3-digit '+number' parses but is too short to be a possible phone
    in any region."""
    with pytest.raises(InvalidPhoneNumberError):
        normalize_phone("+123")


def test_garbage_with_plus_raises() -> None:
    with pytest.raises(InvalidPhoneNumberError):
        normalize_phone("+0")
