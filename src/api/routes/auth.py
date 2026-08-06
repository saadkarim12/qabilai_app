"""Authentication routes: /auth/login, /auth/logout, /auth/me."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import CurrentPrincipal, get_current_principal, get_current_user
from src.db.models.user import User
from src.db.session import get_db
from src.schemas.auth import LoginRequest, LoginResponse, MeResponse
from src.services.auth_service import login, logout

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login_endpoint(
    body: LoginRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LoginResponse:
    token, expires_at = await login(
        db,
        email=body.email,
        password=body.password,
        ip_address=_client_ip(request),
    )
    await db.commit()
    return LoginResponse(access_token=token, expires_at=expires_at)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_endpoint(
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await logout(db, principal.auth_session.id)
    await db.commit()


@router.get("/me", response_model=MeResponse)
async def me_endpoint(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    return current_user


def _client_ip(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host
