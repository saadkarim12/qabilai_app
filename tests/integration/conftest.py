"""Integration-test fixtures.

``migrations_applied`` runs ``alembic upgrade head`` against the configured
DATABASE_URL exactly once per session. ``db_session`` yields a per-test
AsyncSession whose enclosing transaction is rolled back on teardown, so
tests can write freely without leaving rows behind.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import engine, get_db
from src.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def migrations_applied() -> None:
    """Apply all Alembic migrations to the test DB exactly once per session.

    Runs ``alembic`` in a subprocess so its internal ``asyncio.run`` call
    doesn't collide with pytest-asyncio's event-loop management.
    """
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        cwd=REPO_ROOT,
    )


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession whose outer transaction is rolled back on teardown.

    Per-test connection (NullPool in test env), one outer transaction begun
    immediately. The session does not commit — tests typically just ``flush()``
    to round-trip — and teardown always rolls the outer transaction back, so
    the dev database stays clean across test runs.
    """
    async with engine.connect() as conn:
        outer = await conn.begin()
        # ``join_transaction_mode="create_savepoint"`` makes route-level
        # ``session.commit()`` calls release a savepoint rather than
        # committing the outer transaction, so teardown can still roll
        # back every change the test made.
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if outer.is_active:
                await outer.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """Async HTTP client running the FastAPI app in the test's event loop.

    Routes share the test's ``db_session`` via ``dependency_overrides`` so
    setup writes and route writes happen inside one outer transaction —
    teardown rolls everything back.
    """
    app = create_app()

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()
