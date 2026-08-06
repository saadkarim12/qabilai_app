"""Authentication service.

Pure functions over an ``AsyncSession`` — no FastAPI types. The route layer
owns request/response shaping and ``commit()``; this module raises domain
exceptions on failure.

TODO(rate-limit): the architecture doc calls for an in-memory per-IP
attempt limiter (3 fails → 30 s lockout) as a placeholder until Azure Front
Door is in front of the API. Deferred — none of Step 1.3's acceptance
criteria depend on it, and the production limiter will be external anyway.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.session import AuthSession
from src.db.models.user import User
from src.exceptions import InvalidCredentialsError, InvalidTokenError
from src.utils.jwt import create_access_token, decode_access_token, hash_token
from src.utils.password import verify_password

_SESSION_LIFETIME = timedelta(hours=8)


async def login(
    db: AsyncSession,
    email: str,
    password: str,
    ip_address: str,
) -> tuple[str, datetime]:
    """Authenticate and create a session; return the JWT and its expiry.

    Raises :class:`InvalidCredentialsError` (HTTP 401) on either wrong email
    or wrong password — same exception either way so the response can't be
    used to enumerate accounts.
    """
    user = (
        await db.execute(sa.select(User).where(User.email == email.strip().lower()))
    ).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentialsError("Invalid email or password")

    now = datetime.now(UTC)
    expires_at = now + _SESSION_LIFETIME
    session_id = uuid.uuid4()
    token = create_access_token(user.id, session_id, expires_in=_SESSION_LIFETIME)

    db.add(
        AuthSession(
            id=session_id,
            user_id=user.id,
            token_hash=hash_token(token),
            ip_address=ip_address,
            expires_at=expires_at,
        )
    )
    user.last_login_at = now

    await db.flush()
    return token, expires_at


async def logout(db: AsyncSession, auth_session_id: uuid.UUID) -> None:
    """Mark the session revoked. No-op if already revoked or missing."""
    auth_session = await db.get(AuthSession, auth_session_id)
    if auth_session is None or auth_session.revoked_at is not None:
        return
    auth_session.revoked_at = datetime.now(UTC)
    await db.flush()


async def resolve_principal(db: AsyncSession, token: str) -> tuple[User, AuthSession]:
    """Decode the JWT, validate the session is alive, and load the user.

    Every failure raises :class:`InvalidTokenError` (HTTP 401) so the
    response body is identical for missing / revoked / expired sessions —
    nothing leaks about whether a session ever existed.
    """
    payload = decode_access_token(token)

    auth_session = await db.get(AuthSession, payload.session_id)
    if auth_session is None:
        raise InvalidTokenError("Session not found")
    if auth_session.revoked_at is not None:
        raise InvalidTokenError("Session revoked")
    # expires_at is stored timezone-aware; compare against UTC now.
    if auth_session.expires_at <= datetime.now(UTC):
        raise InvalidTokenError("Session expired")

    user = await db.get(User, payload.user_id)
    if user is None:
        raise InvalidTokenError("User no longer exists")

    return user, auth_session
