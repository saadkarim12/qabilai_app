"""``ApplicationScore`` ORM model — append-only score audit.

One row per scoring attempt. The architecture doc spells this out:

    | application_id | UUID FK applications     |
    | score_type     | 'similarity' / 'hard_filter' / 'authenticity' |
    | value          | float                    |
    | breakdown      | jsonb (per-signal data)  |
    | prompt_version | varchar                  |
    | model_used     | varchar                  |
    | computed_at    | timestamptz              |

Append-only: the writer never UPDATEs an existing row, it always
inserts. That keeps the audit trail honest (re-scores are visible) and
sidesteps the need for row-level locks on the score history.

The denormalised columns on ``applications`` (``similarity_score``,
``hard_filter_score``, ``hard_filter_breakdown``) hold the *latest*
attempt for fast list/sort; this table holds the *history*.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import UUID, DateTime, Enum, Float, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base
from src.enums.score_types import ScoreType

PROMPT_VERSION_MAX_LENGTH: Final[int] = 64
MODEL_USED_MAX_LENGTH: Final[int] = 64


def _enum_column(enum_cls: type[StrEnum]) -> Enum:
    """Varchar-backed enum column storing ``.value`` (not ``.name``)."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=32,
        create_constraint=False,
        values_callable=lambda cls: [e.value for e in cls],
    )


class ApplicationScore(Base):
    """One scoring attempt for one application. Append-only audit row."""

    __tablename__ = "application_scores"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey(
            "applications.id",
            ondelete="CASCADE",
            name="fk_application_scores_application",
        ),
        nullable=False,
    )

    score_type: Mapped[ScoreType] = mapped_column(_enum_column(ScoreType), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    breakdown: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    prompt_version: Mapped[str] = mapped_column(String(PROMPT_VERSION_MAX_LENGTH), nullable=False)
    model_used: Mapped[str] = mapped_column(String(MODEL_USED_MAX_LENGTH), nullable=False)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:
        return (
            f"ApplicationScore(id={self.id!r}, application_id={self.application_id!r}, "
            f"score_type={self.score_type!r}, value={self.value!r}, "
            f"model_used={self.model_used!r})"
        )
