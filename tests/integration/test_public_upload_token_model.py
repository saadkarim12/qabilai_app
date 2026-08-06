"""Integration tests for the PublicUploadToken model."""

from __future__ import annotations

import secrets
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.job import Job
from src.db.models.public_upload_token import PublicUploadToken
from src.db.models.user import User
from src.enums.jobs import EmploymentType, WorkMode
from src.enums.roles import UserRole

pytestmark = pytest.mark.integration


async def _seed_job(db_session: AsyncSession) -> Job:
    user = User(
        email=f"hm-{uuid.uuid4().hex[:8]}@kabil.dev",
        password_hash="x",
        full_name="HM",
        role=UserRole.ADMIN,
    )
    db_session.add(user)
    await db_session.flush()

    job = Job(
        created_by=user.id,
        title="Job",
        hiring_company="Co",
        country="ID",
        city="Jakarta",
        employment_type=EmploymentType.PERMANENT,
        work_mode=WorkMode.HYBRID,
        currency="IDR",
        min_experience_years=0,
        job_description="...",
        public_slug=f"slug-{uuid.uuid4().hex[:16]}",
    )
    db_session.add(job)
    await db_session.flush()
    return job


async def test_token_insert_and_select(migrations_applied: None, db_session: AsyncSession) -> None:
    job = await _seed_job(db_session)
    slug = secrets.token_urlsafe(12)
    db_session.add(PublicUploadToken(job_id=job.id, slug=slug))
    await db_session.flush()

    loaded = (
        await db_session.execute(sa.select(PublicUploadToken).where(PublicUploadToken.slug == slug))
    ).scalar_one()
    assert loaded.job_id == job.id
    assert loaded.slug == slug
    assert loaded.is_active is True
    assert loaded.created_at is not None


async def test_slug_is_unique(migrations_applied: None, db_session: AsyncSession) -> None:
    job = await _seed_job(db_session)
    slug = secrets.token_urlsafe(12)
    db_session.add(PublicUploadToken(job_id=job.id, slug=slug))
    await db_session.flush()

    db_session.add(PublicUploadToken(job_id=job.id, slug=slug))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_token_cascades_on_job_delete(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    job = await _seed_job(db_session)
    slug = secrets.token_urlsafe(12)
    db_session.add(PublicUploadToken(job_id=job.id, slug=slug))
    await db_session.flush()

    await db_session.execute(sa.delete(Job).where(Job.id == job.id))
    await db_session.flush()

    row = (
        await db_session.execute(sa.select(PublicUploadToken).where(PublicUploadToken.slug == slug))
    ).one_or_none()
    assert row is None
