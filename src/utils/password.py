"""Bcrypt password hashing helpers.

Cost factor 12 yields roughly 250 ms per hash on modern hardware — the
architecture doc's target for v1. Both hash and verify use bytes-level
APIs from the ``bcrypt`` library so comparison is constant-time.
"""

from __future__ import annotations

import bcrypt

_BCRYPT_ROUNDS = 12


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of ``plain``. Generates a fresh salt each call."""
    return bcrypt.hashpw(
        plain.encode("utf-8"),
        bcrypt.gensalt(rounds=_BCRYPT_ROUNDS),
    ).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time compare ``plain`` against a stored bcrypt ``hashed``.

    Returns ``False`` (never raises) on malformed input so callers can treat
    "wrong password" and "corrupt hash" the same way at the boundary.
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
