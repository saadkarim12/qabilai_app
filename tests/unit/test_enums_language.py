"""Unit tests for ``LanguageCode``."""

from __future__ import annotations

from src.enums.language import LanguageCode


def test_language_code_values() -> None:
    assert {e.value for e in LanguageCode} == {"en", "ar", "mixed"}


def test_language_code_str_enum() -> None:
    assert LanguageCode.EN == "en"
    assert LanguageCode.AR == "ar"
    assert LanguageCode.MIXED == "mixed"
