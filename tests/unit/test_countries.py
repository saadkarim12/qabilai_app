"""Unit tests for ``src.utils.countries``."""

from __future__ import annotations

from src.utils.countries import ISO_3166_ALPHA2, is_valid_country


def test_set_contains_well_known_codes() -> None:
    for code in ("AE", "US", "GB", "PK", "IN", "ID"):
        assert is_valid_country(code), f"{code} should be valid"


def test_rejects_garbage() -> None:
    assert not is_valid_country("XX")
    assert not is_valid_country("")
    assert not is_valid_country("ZZZ")
    assert not is_valid_country("1A")


def test_case_sensitive() -> None:
    # pycountry stores upper-case; the caller is expected to upper-case
    # first. ``JobCreateRequest`` does this via a field_validator.
    assert is_valid_country("AE")
    assert not is_valid_country("ae")


def test_set_is_non_trivial_size() -> None:
    # ISO-3166 has ~249 codes; assert we have at least 240 to catch a
    # broken pycountry import without being brittle on the exact number.
    assert len(ISO_3166_ALPHA2) >= 240


def test_frozenset_is_immutable() -> None:
    assert isinstance(ISO_3166_ALPHA2, frozenset)
