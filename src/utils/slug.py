"""Public-slug generator for the ``/apply/{slug}`` URL.

A slug is a URL-safe random token derived from cryptographic randomness.
At ``SLUG_BYTES = 12`` we get 16 characters and ~96 bits of entropy — the
probability of a collision before ~2^48 jobs is negligible, so we don't
bother with a retry loop. If the unique constraint ever fires, the
underlying ``IntegrityError`` will surface a 500, which is the right
signal that something has gone weirdly wrong.

Length stays well under the ``public_slug`` column's 64-char cap.
"""

from __future__ import annotations

import secrets
from typing import Final

SLUG_BYTES: Final[int] = 12


def generate_public_slug() -> str:
    """Return a 16-char URL-safe slug suitable for ``Job.public_slug``."""
    return secrets.token_urlsafe(SLUG_BYTES)
