"""User role enumeration."""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """Roles a User can take. v1 is single-role per user."""

    ADMIN = "admin"
    HIRING_MANAGER = "hiring_manager"
