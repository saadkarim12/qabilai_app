"""Bootstrap script: create the first HR admin user.

Usage::

    .venv/bin/python -m scripts.seed_admin
    # or
    make seed

The script refuses to run if any user already exists, so it's safe to
re-invoke. The interactive prompts validate the email format, the
full-name presence, and enforce a 12-character minimum on the password.
"""

from __future__ import annotations

import asyncio
import getpass
import sys
from typing import Final

import sqlalchemy as sa
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.user import User
from src.db.session import SessionFactory
from src.enums.roles import UserRole
from src.utils.password import hash_password

MIN_PASSWORD_LENGTH: Final[int] = 12


class AlreadySeededError(Exception):
    """Raised when ``seed_admin`` is called against a non-empty ``users`` table."""


class WeakPasswordError(ValueError):
    """Raised when a password is shorter than :data:`MIN_PASSWORD_LENGTH`."""


async def users_exist(db: AsyncSession) -> bool:
    """Return True if the ``users`` table has at least one row."""
    count = (await db.execute(sa.select(sa.func.count()).select_from(User))).scalar_one()
    return count > 0


async def seed_admin(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
) -> User:
    """Insert the first admin user.

    Defense-in-depth: re-checks the password length and the empty-table
    invariant inside the function so it's safe to call programmatically
    without going through the CLI prompts.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")

    if await users_exist(db):
        raise AlreadySeededError("at least one user already exists")

    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        role=UserRole.ADMIN,
    )
    db.add(user)
    await db.commit()
    return user


def _read_email() -> str:
    adapter: TypeAdapter[EmailStr] = TypeAdapter(EmailStr)
    while True:
        raw = input("Admin email: ").strip()
        try:
            return str(adapter.validate_python(raw))
        except ValidationError:
            print("  Invalid email. Try again.")


def _read_name() -> str:
    while True:
        name = input("Admin full name: ").strip()
        if name:
            return name
        print("  Name cannot be empty.")


def _read_password() -> str:
    while True:
        pw = getpass.getpass(f"Admin password (min {MIN_PASSWORD_LENGTH} chars): ")
        if len(pw) < MIN_PASSWORD_LENGTH:
            print(f"  Too short — need at least {MIN_PASSWORD_LENGTH} characters.")
            continue
        confirm = getpass.getpass("Confirm password: ")
        if pw != confirm:
            print("  Passwords don't match. Try again.")
            continue
        return pw


async def main() -> int:
    print("Kabil.ai — admin seed script\n")

    # Pre-flight: don't waste the operator's time on prompts if there's
    # already a user. The seed_admin call below re-checks atomically in
    # case of a concurrent insert.
    async with SessionFactory() as db:
        if await users_exist(db):
            print("Refusing to seed: at least one user already exists.")
            return 0

    email = _read_email()
    full_name = _read_name()
    password = _read_password()

    async with SessionFactory() as db:
        try:
            await seed_admin(db, email=email, password=password, full_name=full_name)
        except AlreadySeededError:
            print("Refusing to seed: a user was created concurrently.")
            return 0

    print(f"\nCreated admin user: {email}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
