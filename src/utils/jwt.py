"""JWT signing + verification and session-token hashing.

JWTs are HS256-signed with ``settings.app_secret_key``. The payload carries
only the user_id (``sub``), session_id (``sid``), issued-at (``iat``) and
expiry (``exp``) claims — no roles or permissions are baked in, so an
authorization change takes effect on the next request rather than waiting
for the token to expire.

``hash_token`` returns the SHA-256 hex digest of a JWT, used as the
``sessions.token_hash`` column so we can look up the session for revocation
without storing the raw token.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from jose import ExpiredSignatureError, JWTError, jwt
from pydantic import BaseModel

from src.config import settings
from src.exceptions import InvalidTokenError

_ALGORITHM = "HS256"
_DEFAULT_EXPIRY = timedelta(hours=8)


class TokenPayload(BaseModel):
    """Decoded JWT payload — the only fields we actually use."""

    user_id: UUID
    session_id: UUID
    exp: int


def create_access_token(
    user_id: UUID,
    session_id: UUID,
    expires_in: timedelta = _DEFAULT_EXPIRY,
) -> str:
    """Mint a signed JWT carrying the given identifiers."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "iat": int(now.timestamp()),
        "exp": int((now + expires_in).timestamp()),
    }
    # jose's stubs return Any; explicit cast keeps mypy --strict happy.
    return cast(str, jwt.encode(payload, settings.app_secret_key, algorithm=_ALGORITHM))


def decode_access_token(token: str) -> TokenPayload:
    """Verify and decode a JWT.

    Raises :class:`InvalidTokenError` for any expired, tampered or malformed
    token; the global handler returns 401.
    """
    try:
        payload = jwt.decode(token, settings.app_secret_key, algorithms=[_ALGORITHM])
    except ExpiredSignatureError as exc:
        raise InvalidTokenError("Token has expired") from exc
    except JWTError as exc:
        raise InvalidTokenError(f"Invalid token: {exc}") from exc

    try:
        return TokenPayload(
            user_id=UUID(payload["sub"]),
            session_id=UUID(payload["sid"]),
            exp=int(payload["exp"]),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidTokenError(f"Malformed token payload: {exc}") from exc


def hash_token(token: str) -> str:
    """SHA-256 hex digest of ``token`` for the ``sessions.token_hash`` column."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
