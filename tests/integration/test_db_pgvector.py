"""Integration tests for pgvector enablement.

Requires the dev Postgres container to be running (``make up``). Verifies:

1. ``alembic upgrade head`` runs cleanly (via the ``migrations_applied`` fixture).
2. The ``vector`` extension is installed in the connected database.
3. A ``vector(3)`` column accepts INSERT and round-trips on SELECT.

A TEMP table is used so nothing persists in the dev database.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from src.db.session import SessionFactory

pytestmark = pytest.mark.integration


async def test_vector_extension_installed(migrations_applied: None) -> None:
    async with SessionFactory() as session:
        result = await session.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
        assert result.scalar() == 1


async def test_vector_insert_select_roundtrip(migrations_applied: None) -> None:
    async with SessionFactory() as session:
        await session.execute(
            text("CREATE TEMP TABLE _v_smoke (id serial PRIMARY KEY, v vector(3))")
        )
        await session.execute(text("INSERT INTO _v_smoke (v) VALUES ('[1,2,3]')"))
        result = await session.execute(text("SELECT vector_dims(v), v::text FROM _v_smoke"))
        dims, value = result.one()
        assert dims == 3
        assert value == "[1,2,3]"
