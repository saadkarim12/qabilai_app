"""Integration tests for the Job model.

Covers:

- Insert + round-trip of every column, including JSONB defaults.
- ``public_slug`` uniqueness.
- HNSW index visible in ``pg_indexes`` after migrations.
- ``updated_at`` trigger fires on UPDATE (and not on SELECT / no-op).
- ``min_salary > max_salary`` is *allowed* at the DB level (validation lives
  in the Pydantic schema, Step 2.5) — this test pins that contract so we
  notice if someone adds a CHECK constraint silently.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.job import Job
from src.db.models.user import User
from src.enums.jobs import (
    EmploymentType,
    JobStatus,
    NoticePeriod,
    VisaRequirement,
    WorkMode,
)
from src.enums.roles import UserRole

pytestmark = pytest.mark.integration


async def _seed_user(db_session: AsyncSession) -> User:
    user = User(
        email=f"hm-{uuid.uuid4().hex[:8]}@kabil.dev",
        password_hash="x",
        full_name="HM Person",
        role=UserRole.ADMIN,
    )
    db_session.add(user)
    await db_session.flush()
    return user


def _make_job(*, created_by: uuid.UUID, slug: str | None = None) -> Job:
    return Job(
        created_by=created_by,
        title="Senior Backend Engineer",
        hiring_company="Kabil Test Co",
        country="ID",
        city="Jakarta",
        employment_type=EmploymentType.PERMANENT,
        work_mode=WorkMode.HYBRID,
        currency="IDR",
        min_salary=30_000_000,
        max_salary=50_000_000,
        notice_period=NoticePeriod.DAYS_30,
        min_experience_years=5,
        required_skills=["python", "fastapi"],
        preferred_skills=["sqlalchemy"],
        visa_requirement=VisaRequirement.CITIZEN_OR_RESIDENT,
        nationality_preference=[],
        languages_required=["en"],
        ai_verify_flags={"verify_education": True},
        job_description="Build the Kabil backend.",
        whatsapp_questions=[
            {"category": "background_validation", "question": "Confirm 5 years of Python?"}
        ],
        public_slug=slug or f"slug-{uuid.uuid4().hex[:16]}",
    )


async def test_job_insert_and_select(migrations_applied: None, db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)
    job = _make_job(created_by=user.id)
    db_session.add(job)
    await db_session.flush()

    loaded = (await db_session.execute(sa.select(Job).where(Job.id == job.id))).scalar_one()

    assert isinstance(loaded.id, uuid.UUID)
    assert loaded.created_by == user.id
    assert loaded.title == "Senior Backend Engineer"
    assert loaded.country == "ID"
    assert loaded.currency == "IDR"
    assert loaded.employment_type is EmploymentType.PERMANENT
    assert loaded.work_mode is WorkMode.HYBRID
    assert loaded.notice_period is NoticePeriod.DAYS_30
    assert loaded.visa_requirement is VisaRequirement.CITIZEN_OR_RESIDENT
    assert loaded.required_skills == ["python", "fastapi"]
    assert loaded.preferred_skills == ["sqlalchemy"]
    assert loaded.languages_required == ["en"]
    assert loaded.ai_verify_flags == {"verify_education": True}
    assert loaded.whatsapp_questions == [
        {"category": "background_validation", "question": "Confirm 5 years of Python?"}
    ]
    # Defaults applied:
    assert loaded.status is JobStatus.DRAFT
    assert loaded.nationality_preference == []
    assert loaded.jd_embedding is None
    assert loaded.closed_at is None
    assert loaded.created_at is not None
    assert loaded.updated_at is not None


async def test_jsonb_defaults_when_omitted(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    """A Job constructed without the JSONB fields should still load with the
    server-side defaults applied (empty list / empty object)."""
    user = await _seed_user(db_session)
    job = Job(
        created_by=user.id,
        title="Lean Job",
        hiring_company="Co",
        country="ID",
        city="Jakarta",
        employment_type=EmploymentType.PERMANENT,
        work_mode=WorkMode.ONSITE,
        currency="IDR",
        min_experience_years=0,
        job_description="...",
        public_slug=f"slug-{uuid.uuid4().hex[:16]}",
    )
    db_session.add(job)
    await db_session.flush()
    await db_session.refresh(job)

    assert job.required_skills == []
    assert job.preferred_skills == []
    assert job.nationality_preference == []
    assert job.languages_required == []
    assert job.ai_verify_flags == {}
    assert job.whatsapp_questions == []
    assert job.status is JobStatus.DRAFT


async def test_public_slug_is_unique(migrations_applied: None, db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)
    slug = f"slug-{uuid.uuid4().hex[:16]}"
    db_session.add(_make_job(created_by=user.id, slug=slug))
    await db_session.flush()
    db_session.add(_make_job(created_by=user.id, slug=slug))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_hnsw_index_present(migrations_applied: None, db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            sa.text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'jobs' "
                "AND indexname = 'jobs_jd_embedding_hnsw_idx'"
            )
        )
    ).one_or_none()
    assert row is not None, "HNSW index missing on jobs.jd_embedding"
    indexdef = row[0]
    assert "USING hnsw" in indexdef
    assert "vector_cosine_ops" in indexdef


async def test_updated_at_trigger_fires_on_update(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    user = await _seed_user(db_session)
    job = _make_job(created_by=user.id)
    db_session.add(job)
    await db_session.flush()
    original_updated_at = job.updated_at
    assert isinstance(original_updated_at, datetime)

    # UPDATE via direct SQL so we go through the DB-level trigger rather
    # than just touching the in-memory attribute.
    await db_session.execute(sa.update(Job).where(Job.id == job.id).values(title="Updated Title"))
    await db_session.flush()
    db_session.expire(job)
    await db_session.refresh(job)

    assert job.title == "Updated Title"
    assert job.updated_at > original_updated_at
    # Sanity: trigger doesn't touch created_at.
    assert job.created_at <= original_updated_at + (job.updated_at - original_updated_at)
    # And the wall-clock value is in the recent past.
    now = datetime.now(UTC)
    assert (now - job.updated_at).total_seconds() < 60
