"""Enumerations for parsed-CV records.

The CV parser (Step 3.4) produces a :class:`~src.schemas.parsed_cv.ParsedCV`
on every upload. When Claude's response can't be coerced into a clean
schema after the strict retry, the parser falls back to field-by-field
salvage and marks the result :data:`ParseStatus.NEEDS_REVIEW` so HR can
inspect it. ``OK`` is the happy-path label — schema validated on first try
or after the one allowed retry.
"""

from __future__ import annotations

from enum import StrEnum


class ParseStatus(StrEnum):
    """Outcome of running Claude on a CV's extracted text."""

    OK = "ok"
    NEEDS_REVIEW = "needs_review"
