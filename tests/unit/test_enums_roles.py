"""Tests for the UserRole enum."""

from __future__ import annotations

import pytest

from src.enums.roles import UserRole


def test_role_values() -> None:
    assert UserRole.ADMIN == "admin"
    assert UserRole.HIRING_MANAGER == "hiring_manager"


def test_role_constructor_accepts_value() -> None:
    assert UserRole("admin") is UserRole.ADMIN
    assert UserRole("hiring_manager") is UserRole.HIRING_MANAGER


def test_role_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        UserRole("super_user")
