"""Candidate ORM model.

A Candidate is a person who has applied to one or more jobs. Identity
is the composite ``(LOWER(email), phone_e164)`` — enforced via a
functional UNIQUE index in Alembic migration ``0007_candidates``. Two
distinct people can share an email (one number) or a phone (one email)
but not both at once.

Either ``email`` *or* ``phone_e164`` may be NULL (but not both): the HR
bulk uploader accepts a CV that yields only one contact field. Identity
then degrades gracefully — migration ``0013`` swaps the single composite
UNIQUE for three *partial* unique indexes (both-present / email-only /
phone-only) so a phone-only candidate dedups on phone alone, an
email-only candidate on email alone, and a full candidate on the pair.

``parsed_profile`` is populated by the Step 3.4 CV-parsing service;
``authenticity_*`` columns by the Phase 4 scoring pipeline. All four
default to "not yet computed" — NULL where typed, ``{}`` for the JSONB
profile so consumers don't need a separate "is this populated?" check.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import UUID, DateTime, Enum, Float, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, validates

from src.db.base import Base
from src.enums.authenticity import AuthenticityBand

EMAIL_MAX_LENGTH: Final[int] = 254  # RFC 5321 hard cap
PHONE_E164_MAX_LENGTH: Final[int] = 20  # +<country><up to 15 digits> plus buffer
FULL_NAME_MAX_LENGTH: Final[int] = 255


def _enum_column(enum_cls: type[StrEnum]) -> Enum:
    """Varchar-backed enum column that stores ``.value`` (not ``.name``)."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=32,
        create_constraint=False,
        values_callable=lambda cls: [e.value for e in cls],
    )


class Candidate(Base):
    """One human across all their applications. Composite-unique on email+phone."""

    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, server_default=text("gen_random_uuid()")
    )

    # --- Identity (partial UNIQUEs on LOWER(email)/phone_e164; see migration
    #     0013 — at least one of email/phone is always present) --------------
    email: Mapped[str | None] = mapped_column(String(EMAIL_MAX_LENGTH), nullable=True)
    phone_e164: Mapped[str | None] = mapped_column(String(PHONE_E164_MAX_LENGTH), nullable=True)
    full_name: Mapped[str] = mapped_column(String(FULL_NAME_MAX_LENGTH), nullable=False)

    # --- AI artifacts (populated by later steps) --------------------------
    # ``authenticity_*`` here is the job-agnostic *baseline* (five signals,
    # no JD Keyword Mirroring) — a CV-level property shared across the
    # candidate's applications and surfaced by the talent pool. The
    # job-aware, six-signal score lives per-application on
    # ``applications.authenticity_*``.
    parsed_profile: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    authenticity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    authenticity_band: Mapped[AuthenticityBand | None] = mapped_column(
        _enum_column(AuthenticityBand), nullable=True
    )
    authenticity_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    authenticity_computed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Lifecycle --------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    @validates("email")
    def _normalize_email(self, _key: str, value: str | None) -> str | None:
        # Mirrors the User model: dedup index uses LOWER(email), and the
        # write path also lower-cases so reads round-trip identically.
        # ``email`` is now optional (phone-only candidates), so pass NULL
        # through untouched rather than calling ``.strip()`` on ``None``.
        if value is None:
            return None
        return value.strip().lower()

    def __repr__(self) -> str:
        # email + phone are quasi-identifiers; allowed in repr for ops/debug.
        return (
            f"Candidate(id={self.id!r}, email={self.email!r}, "
            f"phone_e164={self.phone_e164!r}, full_name={self.full_name!r})"
        )
