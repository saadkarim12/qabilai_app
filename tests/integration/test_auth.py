"""Integration tests for the /auth/* routes."""

from __future__ import annotations

import uuid

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.user import User
from src.enums.roles import UserRole
from src.utils.password import hash_password

pytestmark = pytest.mark.integration


async def _seed_user(
    db_session: AsyncSession, password: str = "hunter2-correct"
) -> tuple[User, str]:
    user = User(
        email=f"hr-{uuid.uuid4().hex[:8]}@kabil.dev",
        password_hash=hash_password(password),
        full_name="HR Person",
        role=UserRole.ADMIN,
    )
    db_session.add(user)
    await db_session.flush()
    return user, password


async def test_login_then_me_then_logout_then_me_401(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)

    # 1. Login
    response = await client.post("/auth/login", json={"email": user.email, "password": password})
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    token = body["access_token"]
    assert token

    headers = {"Authorization": f"Bearer {token}"}

    # 2. /me succeeds
    response = await client.get("/auth/me", headers=headers)
    assert response.status_code == 200
    me = response.json()
    assert me["email"] == user.email
    assert me["full_name"] == "HR Person"
    assert me["role"] == "admin"
    assert me["id"] == str(user.id)

    # 3. Logout
    response = await client.post("/auth/logout", headers=headers)
    assert response.status_code == 204

    # 4. Same JWT but session is now revoked → 401
    response = await client.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


async def test_login_invalid_password_returns_401(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, _ = await _seed_user(db_session)

    # Wrong password and unknown email must return indistinguishable bodies
    # (modulo correlation_id) so the response can't be used to enumerate
    # which field is wrong.
    wrong_pw = await client.post("/auth/login", json={"email": user.email, "password": "wrong"})
    unknown_email = await client.post(
        "/auth/login",
        json={"email": "missing@kabil.dev", "password": "whatever"},
    )

    assert wrong_pw.status_code == 401
    assert unknown_email.status_code == 401

    a = wrong_pw.json()
    b = unknown_email.json()
    a.pop("correlation_id", None)
    b.pop("correlation_id", None)
    assert a == b
    assert a["error"] == "invalid_credentials"


async def test_login_unknown_email_returns_401(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    # No user seeded.
    response = await client.post(
        "/auth/login",
        json={"email": "missing@kabil.dev", "password": "whatever"},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_credentials"


async def test_me_requires_authorization_header(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/auth/me")
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


async def test_me_with_malformed_authorization_returns_401(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/auth/me", headers={"Authorization": "Basic abc"})
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


async def test_me_with_garbage_token_returns_401(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


async def test_login_updates_last_login_at(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    assert user.last_login_at is None

    response = await client.post("/auth/login", json={"email": user.email, "password": password})
    assert response.status_code == 200

    # Round-trip from DB to verify persistence (not just the in-memory attr).
    fetched = (await db_session.execute(sa.select(User).where(User.id == user.id))).scalar_one()
    assert fetched.last_login_at is not None
