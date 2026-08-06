"""CV document ORM model.

One row per uploaded CV. A candidate can accumulate any number of CVs
(re-uploads, talent-pool updates) but exactly one is ``is_current`` at a
time — enforced by a partial UNIQUE index in
``0008_cv_documents``.

``extracted_text`` and ``cv_embedding`` are NULL until Step 3.3 / 3.4
run; a NULL on ``cv_embedding`` is the unambiguous "not yet processed"
signal that downstream cosine-pre-filter queries gate on.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Final

from sqlalchemy import UUID, Boolean, DateTime, Enum, ForeignKey, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base
from src.db.types import Vector
from src.enums.language import LanguageCode

CV_EMBEDDING_DIM: Final[int] = 1536
BLOB_URL_MAX_LENGTH: Final[int] = 500
BLOB_SHA256_LENGTH: Final[int] = 64  # hex sha256 is exactly 64 chars


def _enum_column(enum_cls: type[StrEnum]) -> Enum:
    """Varchar-backed enum column that stores ``.value`` (not ``.name``)."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=16,
        create_constraint=False,
        values_callable=lambda cls: [e.value for e in cls],
    )


class CvDocument(Base):
    """One uploaded CV file (PDF in Azure Blob) and its derived artifacts."""

    __tablename__ = "cv_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, server_default=text("gen_random_uuid()")
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("candidates.id", ondelete="CASCADE", name="fk_cv_documents_candidate"),
        nullable=False,
    )

    # --- Storage --------------------------------------------------------
    blob_url: Mapped[str] = mapped_column(String(BLOB_URL_MAX_LENGTH), nullable=False)
    # hex sha256 of the PDF bytes — enables "same file re-uploaded" dedup.
    blob_sha256: Mapped[str] = mapped_column(String(BLOB_SHA256_LENGTH), nullable=False)

    # --- Derived (populated by later steps) -----------------------------
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    cv_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(CV_EMBEDDING_DIM), nullable=True
    )
    language: Mapped[LanguageCode | None] = mapped_column(_enum_column(LanguageCode), nullable=True)

    # --- Lifecycle ------------------------------------------------------
    # Partial UNIQUE on (candidate_id) WHERE is_current means exactly one
    # row per candidate may have True at any time; many rows may have False.
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:
        return (
            f"CvDocument(id={self.id!r}, candidate_id={self.candidate_id!r}, "
            f"is_current={self.is_current!r}, language={self.language!r})"
        )
