"""Render numeric 0-100 scores / weights as percentage strings for the API.

Scores and weights are stored and computed as plain numbers in [0, 100]
(so sorting and math stay correct); these helpers format them for the API
response only, e.g. ``82.0 -> "82%"``, ``67.5 -> "67.5%"``, ``35.0 ->
"35%"``. Trailing zeros are trimmed and values are shown to at most two
decimals.
"""

from __future__ import annotations

from typing import TypeGuard

# Breakdown keys whose numeric leaves are percentages (per-signal score and
# weight). Everything else in a breakdown dict passes through untouched.
_PERCENT_KEYS = ("score", "weight")


def _is_number(value: object) -> TypeGuard[float]:
    # bool is a subclass of int — exclude it so e.g. ``passes_threshold``
    # would never be formatted if it ever sat under a percent key.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def format_percent(value: float) -> str:
    """Format a numeric percentage as a trimmed string, e.g. ``82.0 -> "82%"``."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def format_percent_optional(value: float | None) -> str | None:
    """Like :func:`format_percent` but passes ``None`` through unchanged."""
    return None if value is None else format_percent(value)


def to_percent(value: object) -> object:
    """Pydantic ``BeforeValidator`` hook: format numbers, pass anything else.

    Idempotent — an already-formatted string (cache round-trip, or a
    value formatted at the construction site) is returned unchanged.
    """
    return format_percent(value) if _is_number(value) else value


def percentify_breakdown(value: object) -> object:
    """Recursively copy a breakdown structure, formatting ``score`` /
    ``weight`` numeric leaves as percentage strings.

    Other keys (reasoning, distance, counts, skill lists, …) and
    non-numeric values pass through unchanged. Builds new containers
    rather than mutating, so the source ORM JSONB stays numeric.
    """
    if isinstance(value, dict):
        return {
            key: (
                format_percent(item)
                if key in _PERCENT_KEYS and _is_number(item)
                else percentify_breakdown(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [percentify_breakdown(item) for item in value]
    return value
