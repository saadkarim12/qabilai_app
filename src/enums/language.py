"""Language enumeration for CV documents and outbound messages."""

from __future__ import annotations

from enum import StrEnum


class LanguageCode(StrEnum):
    """Languages Kabil.ai supports for CV text and WhatsApp templates.

    ``MIXED`` covers bilingual CVs (English + Arabic, common in the Gulf
    market) — language detection in Step 3.3 returns this when neither
    language dominates.
    """

    EN = "en"
    AR = "ar"
    MIXED = "mixed"
