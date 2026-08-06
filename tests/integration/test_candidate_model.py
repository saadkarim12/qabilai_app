"""Integration tests for the Candidate model.

Covers:

- Round-trip insert + every column.
- Composite UNIQUE on ``(LOWER(email), phone_e164)`` enforces dedup
  case-insensitively on email.
- ``updated_at`` trigger fires on UPDATE.
- ``email`` is normalized to lowercase on write (mirrors ``User``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.candidate import Candidate
from src.enums.authenticity import AuthenticityBand

pytestmark = pytest.mark.integration


def _make_candidate(
    *,
    email: str | None = None,
    phone_e164: str | None = None,
) -> Candidate:
    return Candidate(
        email=email or f"cand-{uuid.uuid4().hex[:8]}@kabil.dev",
        phone_e164=phone_e164 or f"+97150{uuid.uuid4().int % 10_000_000:07d}",
        full_name="Test Candidate",
    )


async def test_round_trip_all_columns(migrations_applied: None, db_session: AsyncSession) -> None:
    candidate = Candidate(
        email="Person@Example.COM",
        phone_e164="+971501234567",
        full_name="Jane Doe",
        parsed_profile={"skills": ["python", "sql"], "years_experience": 5},
        authenticity_score=87.5,
        authenticity_band=AuthenticityBand.AUTHENTIC,
        authenticity_breakdown={
            "signals": [{"name": "linkedin_match", "score": 90}],
            "computed_by": "v0.1.0",
        },
        authenticity_computed_at=datetime(2026, 5, 23, 12, 0, tzinfo=UTC),
    )
    db_session.add(candidate)
    await db_session.flush()
    await db_session.refresh(candidate)

    # Email normalised to lower-case on the way in.
    assert candidate.email == "person@example.com"
    assert candidate.phone_e164 == "+971501234567"
    assert candidate.full_name == "Jane Doe"
    assert candidate.parsed_profile == {"skills": ["python", "sql"], "years_experience": 5}
    assert candidate.authenticity_score == 87.5
    assert candidate.authenticity_band is AuthenticityBand.AUTHENTIC
    assert candidate.authenticity_breakdown == {
        "signals": [{"name": "linkedin_match", "score": 90}],
        "computed_by": "v0.1.0",
    }
    assert candidate.authenticity_computed_at == datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
    assert candidate.created_at is not None
    assert candidate.updated_at is not None


async def test_defaults_when_optional_fields_omitted(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    candidate = _make_candidate()
    db_session.add(candidate)
    await db_session.flush()
    await db_session.refresh(candidate)

    # JSONB default is ``{}``; nullable fields stay NULL.
    assert candidate.parsed_profile == {}
    assert candidate.authenticity_score is None
    assert candidate.authenticity_band is None
    assert candidate.authenticity_breakdown is None
    assert candidate.authenticity_computed_at is None


async def test_same_email_same_phone_violates_unique(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    db_session.add(_make_candidate(email="dup@kabil.dev", phone_e164="+971501111111"))
    await db_session.flush()
    db_session.add(_make_candidate(email="dup@kabil.dev", phone_e164="+971501111111"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_email_uniqueness_is_case_insensitive(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    # Two writes that differ only in email case must collide because the
    # functional unique index keys on LOWER(email).
    db_session.add(_make_candidate(email="caseTest@kabil.dev", phone_e164="+971502222222"))
    await db_session.flush()
    db_session.add(_make_candidate(email="CASETEST@kabil.dev", phone_e164="+971502222222"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_same_email_different_phone_allowed(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    db_session.add(_make_candidate(email="shared@kabil.dev", phone_e164="+971503333333"))
    db_session.add(_make_candidate(email="shared@kabil.dev", phone_e164="+971503333334"))
    await db_session.flush()  # No error.


async def test_same_phone_different_email_allowed(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    db_session.add(_make_candidate(email="alice@kabil.dev", phone_e164="+971504444444"))
    db_session.add(_make_candidate(email="bob@kabil.dev", phone_e164="+971504444444"))
    await db_session.flush()


async def test_updated_at_trigger_fires_on_update(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    candidate = _make_candidate()
    db_session.add(candidate)
    await db_session.flush()
    original = candidate.updated_at
    assert isinstance(original, datetime)

    await db_session.execute(
        sa.update(Candidate).where(Candidate.id == candidate.id).values(full_name="New Name")
    )
    await db_session.flush()
    db_session.expire(candidate)
    await db_session.refresh(candidate)

    assert candidate.full_name == "New Name"
    assert candidate.updated_at > original
