"""E.164 phone-number normalization.

Single public entry point :func:`normalize_phone`. It parses a free-form
number — international only — using Google's ``phonenumbers`` library
and returns the canonical E.164 string (``+<country><number>``).

The validator is country-agnostic by default: a candidate from any
country can apply as long as the number is supplied in international
format (``+<country_code><number>``). National formats like
``0501234567`` or ``03001234567`` are rejected because we cannot
reliably infer the country from a bare national number — Kabil.ai is
not bound to a single market.

Single-market deployments can opt into national-format parsing by
passing ``default_region`` (an ISO 3166-1 alpha-2 code such as
``"PK"``); callers source it from ``settings.default_phone_region``.
When set, a bare national number is interpreted in that region and
normalized to E.164 just like an international one.

Numbers are accepted on *possibility* (correct length / shape for the
country code) rather than strict validity, so plausible-but-unassigned
numbers — e.g. fictional ``555`` demo numbers — are accepted. Genuinely
impossible inputs (too short, unparseable, bad country code) still
raise.

Dedup elsewhere in the codebase (the composite UNIQUE index on
``candidates``) relies on every stored number being in this exact
format. There is no fuzzy matching: a number is either parseable and
possible (returned in canonical E.164) or it isn't
(``InvalidPhoneNumberError`` is raised).
"""

from __future__ import annotations

import phonenumbers


class InvalidPhoneNumberError(ValueError):
    """Raised when ``normalize_phone`` can't produce a valid E.164 number."""


def normalize_phone(raw: str, default_region: str | None = None) -> str:
    """Return ``raw`` formatted as an E.164 string.

    Args:
        raw: The phone number as the user typed it. By default it must
            include the ``+`` prefix and country code (e.g. ``+971…``,
            ``+92…``, ``+44…``). Whitespace and formatting (spaces, dashes,
            parens) between digits are fine — the underlying parser strips
            them.
        default_region: Optional ISO 3166-1 alpha-2 region code (e.g.
            ``"PK"``) used to interpret a *national*-format number that
            lacks a country code — ``03001234567`` becomes ``+92…`` when
            this is ``"PK"``. Ignored when ``raw`` already carries a ``+``
            country code. ``None`` (the default) keeps the strict
            international-only behaviour. Callers pass
            ``settings.default_phone_region``.

    Returns:
        E.164 string like ``"+971501234567"``.

    Raises:
        InvalidPhoneNumberError: if ``raw`` is blank, missing the ``+``
            prefix while no ``default_region`` is given, unparseable, or
            parses to an impossible number (wrong length, invalid country
            code, etc.).
    """
    if not raw or not raw.strip():
        raise InvalidPhoneNumberError("phone number is empty")

    cleaned = raw.strip()
    if not cleaned.startswith("+") and not default_region:
        raise InvalidPhoneNumberError(
            "phone must start with + and country code, e.g. +923001234567"
        )

    # A ``+`` prefix carries its own country code, so the region hint is
    # only consulted for bare national numbers.
    region = None if cleaned.startswith("+") else default_region
    try:
        parsed = phonenumbers.parse(cleaned, region)
    except phonenumbers.NumberParseException as exc:
        raise InvalidPhoneNumberError(f"could not parse phone number {raw!r}: {exc}") from exc

    # Possibility (right length / shape for the country code) rather than
    # strict validity, so plausible-but-unassigned numbers — fictional
    # 555 demo numbers, freshly-allocated ranges phonenumbers' metadata
    # doesn't know yet — are accepted. Truly impossible inputs still fail.
    if not phonenumbers.is_possible_number(parsed):
        raise InvalidPhoneNumberError(f"{raw!r} is not a possible phone number")

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
