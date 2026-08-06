"""Custom SQLAlchemy column types.

Re-exports `pgvector.sqlalchemy.Vector` so ORM models import vector columns
from a single internal path. The `vector` Postgres extension is enabled by
Alembic migration ``0001_enable_pgvector``.
"""

from __future__ import annotations

from pgvector.sqlalchemy import Vector

__all__ = ["Vector"]
