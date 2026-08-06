"""Unit tests for the User ORM model (no DB)."""

from __future__ import annotations

from src.db.models.user import User
from src.enums.roles import UserRole


def test_email_is_lowercased_on_set() -> None:
    user = User(
        email="HR@KABIL.COM",
        password_hash="x",
        full_name="HR Person",
        role=UserRole.ADMIN,
    )
    assert user.email == "hr@kabil.com"


def test_email_is_stripped_on_set() -> None:
    user = User(
        email="  hr@kabil.com  ",
        password_hash="x",
        full_name="HR",
        role=UserRole.ADMIN,
    )
    assert user.email == "hr@kabil.com"


def test_repr_excludes_password_hash() -> None:
    user = User(
        email="hr@kabil.com",
        password_hash="bcrypt-secret-do-not-leak",
        full_name="HR",
        role=UserRole.ADMIN,
    )
    text = repr(user)
    assert "bcrypt-secret-do-not-leak" not in text
    assert "password_hash" not in text
    # Other fields ARE present — repr should still be useful for debugging.
    assert "hr@kabil.com" in text
    assert "HR" in text


def test_role_assignment_preserves_enum() -> None:
    user = User(
        email="a@b.c",
        password_hash="x",
        full_name="x",
        role=UserRole.HIRING_MANAGER,
    )
    assert user.role is UserRole.HIRING_MANAGER
