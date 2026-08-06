"""Integration tests for the User model against a real Postgres."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.user import User
from src.enums.roles import UserRole

pytestmark = pytest.mark.integration


async def test_user_insert_and_select(migrations_applied: None, db_session: AsyncSession) -> None:
    email = f"hr-{uuid.uuid4().hex[:8]}@kabil.dev"
    user = User(
        email=email,
        password_hash="hashed-value",
        full_name="Test HR",
        role=UserRole.ADMIN,
    )
    db_session.add(user)
    await db_session.flush()

    result = await db_session.execute(sa.select(User).where(User.email == email))
    loaded = result.scalar_one()

    assert isinstance(loaded.id, uuid.UUID)
    assert loaded.email == email
    assert loaded.full_name == "Test HR"
    assert loaded.role is UserRole.ADMIN
    assert loaded.password_hash == "hashed-value"
    assert loaded.created_at is not None
    assert loaded.last_login_at is None


async def test_email_is_unique(migrations_applied: None, db_session: AsyncSession) -> None:
    email = f"dup-{uuid.uuid4().hex[:8]}@kabil.dev"
    db_session.add(
        User(
            email=email,
            password_hash="x",
            full_name="A",
            role=UserRole.ADMIN,
        )
    )
    await db_session.flush()

    db_session.add(
        User(
            email=email,
            password_hash="y",
            full_name="B",
            role=UserRole.ADMIN,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_email_persists_lowercased(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    raw_email = f"UPPER-{uuid.uuid4().hex[:8]}@KABIL.DEV"
    user = User(
        email=raw_email,
        password_hash="x",
        full_name="X",
        role=UserRole.ADMIN,
    )
    db_session.add(user)
    await db_session.flush()

    # Force a DB round-trip so we verify the lowercased value persisted.
    db_session.expire(user)
    await db_session.refresh(user)
    assert user.email == raw_email.lower()
