"""Integration tests for the AuthSession model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.session import AuthSession
from src.db.models.user import User
from src.enums.roles import UserRole

pytestmark = pytest.mark.integration


async def _seed_user(db_session: AsyncSession) -> User:
    user = User(
        email=f"hr-{uuid.uuid4().hex[:8]}@kabil.dev",
        password_hash="hashed",
        full_name="HR",
        role=UserRole.ADMIN,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def test_session_insert_and_select(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    user = await _seed_user(db_session)
    token_hash = "a" * 64
    expires_at = datetime.now(UTC) + timedelta(hours=8)

    auth = AuthSession(
        user_id=user.id,
        token_hash=token_hash,
        ip_address="127.0.0.1",
        expires_at=expires_at,
    )
    db_session.add(auth)
    await db_session.flush()

    loaded = (
        await db_session.execute(sa.select(AuthSession).where(AuthSession.token_hash == token_hash))
    ).scalar_one()

    assert isinstance(loaded.id, uuid.UUID)
    assert loaded.user_id == user.id
    assert loaded.token_hash == token_hash
    assert loaded.ip_address == "127.0.0.1"
    assert loaded.revoked_at is None
    assert loaded.expires_at is not None


async def test_token_hash_is_unique(migrations_applied: None, db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)
    token_hash = "b" * 64
    expires_at = datetime.now(UTC) + timedelta(hours=8)

    db_session.add(
        AuthSession(
            user_id=user.id,
            token_hash=token_hash,
            ip_address="127.0.0.1",
            expires_at=expires_at,
        )
    )
    await db_session.flush()

    db_session.add(
        AuthSession(
            user_id=user.id,
            token_hash=token_hash,  # duplicate
            ip_address="127.0.0.1",
            expires_at=expires_at,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_foreign_key_to_users_enforced(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    db_session.add(
        AuthSession(
            user_id=uuid.uuid4(),  # no user with this id exists
            token_hash="c" * 64,
            ip_address="127.0.0.1",
            expires_at=datetime.now(UTC) + timedelta(hours=8),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
