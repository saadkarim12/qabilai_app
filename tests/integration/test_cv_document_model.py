"""Integration tests for the CvDocument model.

Covers:

- Round-trip insert + select with all columns including vector embedding.
- HNSW index visible in ``pg_indexes``.
- Partial UNIQUE: only one ``is_current=True`` per candidate.
- Multiple ``is_current=False`` rows per candidate are allowed.
- FK CASCADE: deleting a candidate removes their CV rows.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.candidate import Candidate
from src.db.models.cv_document import CV_EMBEDDING_DIM, CvDocument
from src.enums.language import LanguageCode

pytestmark = pytest.mark.integration


async def _seed_candidate(db_session: AsyncSession) -> Candidate:
    candidate = Candidate(
        email=f"cv-{uuid.uuid4().hex[:8]}@kabil.dev",
        phone_e164=f"+97150{uuid.uuid4().int % 10_000_000:07d}",
        full_name="CV Owner",
    )
    db_session.add(candidate)
    await db_session.flush()
    return candidate


def _make_cv(*, candidate_id: uuid.UUID, is_current: bool = True) -> CvDocument:
    blob_id = uuid.uuid4().hex
    return CvDocument(
        candidate_id=candidate_id,
        blob_url=f"cvs/{candidate_id}/{blob_id}.pdf",
        blob_sha256=blob_id + blob_id,  # 64 hex chars
        is_current=is_current,
    )


async def test_round_trip_all_columns(migrations_applied: None, db_session: AsyncSession) -> None:
    candidate = await _seed_candidate(db_session)
    cv = CvDocument(
        candidate_id=candidate.id,
        blob_url=f"cvs/{candidate.id}/abc.pdf",
        blob_sha256="a" * 64,
        extracted_text="Hello world",
        cv_embedding=[0.1] * CV_EMBEDDING_DIM,
        language=LanguageCode.EN,
        is_current=True,
    )
    db_session.add(cv)
    await db_session.flush()
    await db_session.refresh(cv)

    assert cv.candidate_id == candidate.id
    assert cv.blob_url == f"cvs/{candidate.id}/abc.pdf"
    assert cv.blob_sha256 == "a" * 64
    assert cv.extracted_text == "Hello world"
    assert cv.cv_embedding is not None
    assert len(cv.cv_embedding) == CV_EMBEDDING_DIM
    assert cv.language is LanguageCode.EN
    assert cv.is_current is True
    assert cv.uploaded_at is not None


async def test_defaults_when_optional_fields_omitted(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    candidate = await _seed_candidate(db_session)
    cv = _make_cv(candidate_id=candidate.id)
    db_session.add(cv)
    await db_session.flush()
    await db_session.refresh(cv)

    assert cv.extracted_text is None
    assert cv.cv_embedding is None
    assert cv.language is None
    assert cv.is_current is True


async def test_hnsw_index_present(migrations_applied: None, db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            sa.text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'cv_documents' "
                "AND indexname = 'cv_documents_cv_embedding_hnsw_idx'"
            )
        )
    ).one_or_none()
    assert row is not None, "HNSW index missing on cv_documents.cv_embedding"
    indexdef = row[0]
    assert "USING hnsw" in indexdef
    assert "vector_cosine_ops" in indexdef


async def test_partial_unique_blocks_two_current_per_candidate(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    candidate = await _seed_candidate(db_session)
    db_session.add(_make_cv(candidate_id=candidate.id, is_current=True))
    await db_session.flush()

    # Second current CV for the same candidate → IntegrityError.
    db_session.add(_make_cv(candidate_id=candidate.id, is_current=True))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_multiple_non_current_rows_allowed(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    candidate = await _seed_candidate(db_session)
    db_session.add(_make_cv(candidate_id=candidate.id, is_current=False))
    db_session.add(_make_cv(candidate_id=candidate.id, is_current=False))
    db_session.add(_make_cv(candidate_id=candidate.id, is_current=False))
    await db_session.flush()  # No error — partial index only constrains True rows.


async def test_current_can_coexist_with_non_current(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    candidate = await _seed_candidate(db_session)
    db_session.add(_make_cv(candidate_id=candidate.id, is_current=False))
    db_session.add(_make_cv(candidate_id=candidate.id, is_current=True))
    await db_session.flush()


async def test_fk_cascade_deletes_cv_rows(
    migrations_applied: None, db_session: AsyncSession
) -> None:
    candidate = await _seed_candidate(db_session)
    cv = _make_cv(candidate_id=candidate.id)
    db_session.add(cv)
    await db_session.flush()
    cv_id = cv.id

    await db_session.execute(sa.delete(Candidate).where(Candidate.id == candidate.id))
    await db_session.flush()

    remaining = (
        await db_session.execute(sa.select(CvDocument).where(CvDocument.id == cv_id))
    ).scalar_one_or_none()
    assert remaining is None
