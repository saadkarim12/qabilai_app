"""Async database engine, session factory, and FastAPI dependency.

The engine is lazy — `create_async_engine` does not open connections until a
session is actually used, so importing this module has no side effects beyond
constructing in-memory objects.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.config import settings


def _build_engine() -> AsyncEngine:
    connect_args: dict[str, Any] = {}
    url = settings.database_url
    # Only enforce TLS in production and only when the DSN doesn't already
    # specify it — operators retain full control via DATABASE_URL.
    ssl_in_url = "ssl=" in url or "sslmode=" in url
    if settings.is_production and not ssl_in_url:
        connect_args["ssl"] = True

    kwargs: dict[str, Any] = {"pool_pre_ping": True, "connect_args": connect_args}
    if settings.app_env == "test" or settings.db_use_null_pool:
        # Two contexts share this branch:
        # * tests — pytest-asyncio uses per-test event loops, and asyncpg
        #   connections are tied to the loop that created them.
        # * Celery worker — each task bridges sync->async via
        #   ``asyncio.run``, which creates a fresh event loop every call.
        # NullPool means each session checkout opens a brand-new asyncpg
        # connection on whatever loop is current, dodging the
        # cross-loop-future failure mode.
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_size"] = settings.database_pool_size

    return create_async_engine(url, **kwargs)


engine: AsyncEngine = _build_engine()

SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an `AsyncSession`.

    Routes and services own their own commit semantics; this dependency only
    rolls back on uncaught exceptions and always closes the session.
    """

    async with SessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
