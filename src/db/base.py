"""SQLAlchemy declarative base.

Kept in its own module so models and the session factory can both import
from it without creating cycles.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
