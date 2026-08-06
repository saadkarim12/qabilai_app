"""Pydantic schemas for the /auth/* endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr

from src.enums.roles import UserRole


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    # OAuth2 "token_type" value — not a password.
    token_type: str = "bearer"  # noqa: S105
    expires_at: datetime


class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    role: UserRole
    created_at: datetime
    last_login_at: datetime | None
