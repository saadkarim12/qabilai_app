"""Integration tests for the admin-seed script."""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.seed_admin import (
    AlreadySeededError,
    WeakPasswordError,
    seed_admin,
    users_exist,
)
from src.db.models.application import Application
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.job import Job
from src.db.models.public_upload_token import PublicUploadToken
from src.db.models.session import AuthSession
from src.db.models.user import User
from src.enums.roles import UserRole

pytestmark = pytest.mark.integration

_VALID_PW = "very-secure-pw-123"  # ≥ 12 chars


@pytest_asyncio.fixture
async def _empty_users(db_session: AsyncSession) -> None:
    """Wipe pre-existing users inside the outer test transaction.

    The seed-admin tests assume an empty users table to start. The dev DB
    may carry a real admin (and a job, candidate, application, etc.
    created by manual Postman testing) outside any test. Deleting inside
    the per-test savepoint hides those rows from this session's view;
    teardown rolls the delete back so the bootstrap data survives.

    Order matters: ``users`` is FK'd by ``jobs.created_by`` and
    ``sessions.user_id``; jobs are FK'd by ``applications`` and
    ``public_upload_tokens``; applications are FK'd by ``cv_documents``
    via ``candidates``. Delete dependents first.
    """
    await db_session.execute(sa.delete(Application))
    await db_session.execute(sa.delete(PublicUploadToken))
    await db_session.execute(sa.delete(CvDocument))
    await db_session.execute(sa.delete(Candidate))
    await db_session.execute(sa.delete(Job))
    await db_session.execute(sa.delete(AuthSession))
    await db_session.execute(sa.delete(User))
    await db_session.flush()


async def test_seed_creates_admin_user(
    migrations_applied: None, db_session: AsyncSession, _empty_users: None
) -> None:
    assert await users_exist(db_session) is False

    user = await seed_admin(
        db_session,
        email="admin@kabil.dev",
        password=_VALID_PW,
        full_name="Admin Person",
    )

    assert user.email == "admin@kabil.dev"
    assert user.role is UserRole.ADMIN
    assert user.password_hash != _VALID_PW  # hashed, not plaintext

    # Round-trip from DB to confirm persistence.
    loaded = (
        await db_session.execute(sa.select(User).where(User.email == "admin@kabil.dev"))
    ).scalar_one()
    assert loaded.id == user.id
    assert loaded.role is UserRole.ADMIN


async def test_seed_refuses_when_users_already_exist(
    migrations_applied: None, db_session: AsyncSession, _empty_users: None
) -> None:
    db_session.add(
        User(
            email="existing@kabil.dev",
            password_hash="x",
            full_name="Existing",
            role=UserRole.ADMIN,
        )
    )
    await db_session.flush()

    with pytest.raises(AlreadySeededError):
        await seed_admin(
            db_session,
            email="another@kabil.dev",
            password=_VALID_PW,
            full_name="Another",
        )


async def test_seed_rejects_short_password(
    migrations_applied: None, db_session: AsyncSession, _empty_users: None
) -> None:
    with pytest.raises(WeakPasswordError):
        await seed_admin(
            db_session,
            email="admin@kabil.dev",
            password="short",  # < 12 chars
            full_name="Admin",
        )

    # And nothing was written.
    assert await users_exist(db_session) is False


async def test_seeded_admin_can_log_in(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    _empty_users: None,
) -> None:
    await seed_admin(
        db_session,
        email="admin@kabil.dev",
        password=_VALID_PW,
        full_name="Admin Person",
    )

    response = await client.post(
        "/auth/login",
        json={"email": "admin@kabil.dev", "password": _VALID_PW},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]
