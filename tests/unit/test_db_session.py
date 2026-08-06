"""Unit tests for the database session module.

Verifies that importing `src.db.session` builds the engine and session
factory correctly and that `get_db` rolls back on exception. No real
database connection is opened.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.db import session as session_module
from src.db.session import SessionFactory, engine, get_db


def test_engine_is_async() -> None:
    assert isinstance(engine, AsyncEngine)


def test_session_factory_yields_async_sessions() -> None:
    assert isinstance(SessionFactory, async_sessionmaker)
    session = SessionFactory()
    assert isinstance(session, AsyncSession)


def test_get_db_is_async_generator_fn() -> None:
    assert inspect.isasyncgenfunction(get_db)


async def test_get_db_rolls_back_on_exception() -> None:
    """If the caller raises inside the `async for`, the session must rollback."""
    mock_session = MagicMock(spec=AsyncSession)
    mock_session.rollback = AsyncMock()

    fake_ctx = AsyncMock()
    fake_ctx.__aenter__.return_value = mock_session
    fake_ctx.__aexit__.return_value = None

    with patch.object(session_module, "SessionFactory", MagicMock(return_value=fake_ctx)):
        agen = session_module.get_db()
        yielded = await agen.__anext__()
        assert yielded is mock_session

        with pytest.raises(RuntimeError, match="boom"):
            await agen.athrow(RuntimeError("boom"))

    mock_session.rollback.assert_awaited_once()
