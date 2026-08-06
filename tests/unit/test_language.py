"""Unit tests for ``src.utils.language``."""

from __future__ import annotations

from src.enums.language import LanguageCode
from src.utils import language

# --- English ---------------------------------------------------------------


def test_pure_english_returns_en() -> None:
    text = (
        "Senior backend engineer with five years of Python and Postgres "
        "experience building distributed systems."
    )
    assert language.detect_language(text) == LanguageCode.EN


def test_short_english_above_threshold_returns_en() -> None:
    # >= MIN_TEXT_LENGTH_FOR_DETECTION (20) chars, no Arabic.
    text = "Hello world this is fine"
    assert len(text) >= language.MIN_TEXT_LENGTH_FOR_DETECTION
    assert language.detect_language(text) == LanguageCode.EN


# --- Arabic ----------------------------------------------------------------


def test_pure_arabic_returns_ar() -> None:
    # "Software engineer with seven years of experience in backend systems"
    # in Arabic.
    text = "مهندس برمجيات لديه سبع سنوات من الخبرة في تطوير الأنظمة الخلفية للشركات الناشئة"
    assert language.detect_language(text) == LanguageCode.AR


def test_short_arabic_text_uses_unicode_fallback() -> None:
    # Below MIN_TEXT_LENGTH_FOR_DETECTION but Arabic chars dominate.
    text = "مرحبا"
    assert len(text) < language.MIN_TEXT_LENGTH_FOR_DETECTION
    assert language.detect_language(text) == LanguageCode.AR


# --- Mixed -----------------------------------------------------------------


def test_balanced_mixed_returns_mixed() -> None:
    # Each side clears 20% of total alphabetic chars.
    text = (
        "Python backend engineer specialising in PostgreSQL and Redis. "
        "مهندس برمجيات لديه خبرة في تطوير الأنظمة الخلفية والقواعد"
    )
    assert language.detect_language(text) == LanguageCode.MIXED


def test_ninety_ten_en_skews_to_en() -> None:
    # ~10% Arabic should be below MIXED_MIN_RATIO=0.2 → falls to langdetect, EN.
    text = (
        "Python backend engineer with extensive experience in Postgres, Redis, "
        "Celery, FastAPI, and distributed systems running on Kubernetes. مرحبا"
    )
    assert language.detect_language(text) == LanguageCode.EN


def test_ninety_ten_ar_skews_to_ar() -> None:
    text = (
        "مهندس برمجيات لديه سبع سنوات من الخبرة في تطوير الأنظمة الخلفية للشركات الناشئة "
        "والمتوسطة الحجم في مجالات التكنولوجيا المالية والصحية والتعليمية والخدمات hi"
    )
    assert language.detect_language(text) == LanguageCode.AR


# --- Edge cases ------------------------------------------------------------


def test_empty_string_returns_en() -> None:
    assert language.detect_language("") == LanguageCode.EN


def test_whitespace_only_returns_en() -> None:
    assert language.detect_language("   \n\t  ") == LanguageCode.EN


def test_punctuation_only_returns_en() -> None:
    # No alphabetic chars at all → total_alpha == 0 → default EN.
    assert language.detect_language("... !!! ??? 12345") == LanguageCode.EN


def test_arabic_unicode_range_boundaries() -> None:
    """Pin the U+0600 to U+06FF bounds against accidental drift."""
    # Just inside the lower bound.
    assert language._arabic_char_count("؀") == 1
    # Just inside the upper bound.
    assert language._arabic_char_count("ۿ") == 1
    # Just outside on the low side.
    assert language._arabic_char_count("׿") == 0
    # Just outside on the high side (Arabic Supplement starts at U+0750).
    assert language._arabic_char_count("܀") == 0


def test_langdetect_is_seeded_and_deterministic() -> None:
    text = (
        "Engineer with experience in distributed systems and event-driven "
        "architecture using Kafka and Postgres."
    )
    first = language.detect_language(text)
    second = language.detect_language(text)
    third = language.detect_language(text)
    assert first == second == third


def test_constants_match_doc() -> None:
    assert language.ARABIC_UNICODE_START == 0x0600
    assert language.ARABIC_UNICODE_END == 0x06FF
    assert language.MIXED_MIN_RATIO == 0.20
    assert language.LANGDETECT_SEED == 0
