"""FastAPI dependencies.

``get_current_principal`` is the single place that parses the
``Authorization`` header and resolves a request to a (User, AuthSession)
pair. Routes that only need the user depend on ``get_current_user``, which
just unwraps the principal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.session import AuthSession
from src.db.models.user import User
from src.db.session import get_db
from src.exceptions import InvalidTokenError
from src.services.auth_service import resolve_principal


@dataclass(frozen=True, slots=True)
class CurrentPrincipal:
    user: User
    auth_session: AuthSession


async def get_current_principal(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentPrincipal:
    """Resolve the authenticated user + session for the current request."""
    if not authorization:
        raise InvalidTokenError("Missing Authorization header")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise InvalidTokenError("Authorization header must be 'Bearer <token>'")

    user, auth_session = await resolve_principal(db, token.strip())
    return CurrentPrincipal(user=user, auth_session=auth_session)


async def get_current_user(
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
) -> User:
    return principal.user


async def get_current_hr_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """HR-only routes depend on this.

    Today it's a thin alias of ``get_current_user`` — both ``admin`` and
    ``hiring_manager`` are considered HR. Centralising the gate here gives
    us one place to tighten (e.g. require ``admin`` for destructive
    actions) without sweeping every route.
    """
    return current_user
