"""ISO-3166 alpha-2 country code validation.

The set of valid codes is built once at import time from ``pycountry`` —
the same source the rest of the Python ecosystem uses for ISO data, so
adding a country (rare, but Kosovo did happen) is a dep bump, not a
hand edit. We freeze the result so callers can't mutate it.
"""

from __future__ import annotations

from typing import Final

import pycountry

ISO_3166_ALPHA2: Final[frozenset[str]] = frozenset(
    country.alpha_2 for country in pycountry.countries
)


def is_valid_country(code: str) -> bool:
    """Return True iff ``code`` is a known ISO-3166 alpha-2 country code.

    Case-sensitive — pycountry stores codes in upper-case (``"AE"``,
    ``"US"``). Callers should upper-case before checking if their input
    might be lower-case.
    """
    return code in ISO_3166_ALPHA2


def country_display_name(code: str) -> str:
    """Human-readable country name for an ISO-3166 alpha-2 code.

    ``"AE"`` → ``"United Arab Emirates"``. Falls back to the code itself for
    anything pycountry doesn't recognise, so this never raises — callers use
    it to render prompts/UI, not to validate. Input is upper-cased first.
    """
    country = pycountry.countries.get(alpha_2=code.upper())
    return country.name if country is not None else code
