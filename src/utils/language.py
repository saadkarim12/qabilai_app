"""Language detection for CV / JD text.

Public entry point: :func:`detect_language` classifies text as
:class:`~src.enums.language.LanguageCode` ``EN`` / ``AR`` / ``MIXED``.

Strategy:

1. Count Arabic chars (``U+0600`` to ``U+06FF``) and ASCII letters. If
   **both** ratios exceed :data:`MIXED_MIN_RATIO`, return ``MIXED`` — this
   is the only reliable way to spot bilingual CVs since ``langdetect``
   never returns a "mixed" label.
2. Otherwise run ``langdetect`` (seeded for determinism) and map ``ar``
   → ``AR``, everything else → ``EN`` (we don't care to enumerate other
   languages; downstream only branches on Arabic vs not).
3. For very short or empty input, default to ``EN`` rather than letting
   ``langdetect`` raise.
"""

from __future__ import annotations

import logging
from typing import Final

from langdetect import DetectorFactory, LangDetectException, detect

from src.enums.language import LanguageCode

logger = logging.getLogger(__name__)

# Seed langdetect's internal RNG so the same input always returns the same
# label. The library docs explicitly recommend this for production use.
LANGDETECT_SEED: Final[int] = 0
DetectorFactory.seed = LANGDETECT_SEED

# Arabic Unicode block (Basic Arabic). Excludes Arabic Supplement and
# Extended-A — those are rare in CV text and not part of the doc's
# acceptance criterion.
ARABIC_UNICODE_START: Final[int] = 0x0600
ARABIC_UNICODE_END: Final[int] = 0x06FF

# Either ratio (Arabic chars / total alpha, or ASCII alpha / total alpha)
# must clear this fraction for the text to be classified as MIXED.
MIXED_MIN_RATIO: Final[float] = 0.20

# Below this character count we don't trust langdetect (it raises on
# fewer than ~3 chars and gets noisy below ~20).
MIN_TEXT_LENGTH_FOR_DETECTION: Final[int] = 20

# Language tag returned by langdetect for Arabic.
_LANGDETECT_ARABIC: Final[str] = "ar"


def _arabic_char_count(text: str) -> int:
    return sum(1 for ch in text if ARABIC_UNICODE_START <= ord(ch) <= ARABIC_UNICODE_END)


def _ascii_alpha_count(text: str) -> int:
    return sum(1 for ch in text if ch.isascii() and ch.isalpha())


def detect_language(text: str) -> LanguageCode:
    """Classify ``text`` as ``EN``, ``AR``, or ``MIXED``."""
    if not text or not text.strip():
        return LanguageCode.EN

    arabic_count = _arabic_char_count(text)
    ascii_count = _ascii_alpha_count(text)
    total_alpha = arabic_count + ascii_count

    if total_alpha == 0:
        return LanguageCode.EN

    arabic_ratio = arabic_count / total_alpha
    ascii_ratio = ascii_count / total_alpha
    if arabic_ratio >= MIXED_MIN_RATIO and ascii_ratio >= MIXED_MIN_RATIO:
        return LanguageCode.MIXED

    # Short text → skip langdetect (it's unreliable). The ratio check
    # above already handled bilingual short snippets; for monolingual
    # short text, fall back to whichever script dominates.
    if len(text) < MIN_TEXT_LENGTH_FOR_DETECTION:
        return LanguageCode.AR if arabic_count > ascii_count else LanguageCode.EN

    try:
        tag = detect(text)
    except LangDetectException:
        logger.warning("language.detect_failed", extra={"text_length": len(text)})
        return LanguageCode.AR if arabic_count > ascii_count else LanguageCode.EN

    return LanguageCode.AR if tag == _LANGDETECT_ARABIC else LanguageCode.EN
