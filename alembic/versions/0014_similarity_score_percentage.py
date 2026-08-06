"""Rescale similarity scores from a 0–1 cosine to a 0–100 percentage.

``similarity_score`` (and the ``application_scores`` audit rows that mirror
it) historically stored raw cosine similarity in ``[0, 1]``. The pipeline
now writes it as a percentage in ``[0, 100]`` so HR reads it as a percent
match. This migration backfills existing data to the new scale:

  - ``applications.similarity_score``      × 100
  - ``application_scores.value``           × 100  (similarity rows only)
  - the similarity breakdown's ``distance`` / ``threshold_similarity`` /
    ``margin`` keys × 100  (legacy ``breakdown = {}`` rows are left alone)

``hard_filter`` and ``authenticity`` scores are already on a 0–100 scale
and are untouched (the WHERE clauses scope every statement to
``score_type = 'similarity'``).

Revision ID: 0014_similarity_score_percentage
Revises: 0013_candidate_nullable_contact
Create Date: 2026-06-08
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0014_similarity_score_percentage"
down_revision: str | None = "0013_candidate_nullable_contact"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Breakdown keys that carry a similarity/distance magnitude and so must be
# rescaled in lockstep with the score itself.
_BREAKDOWN_SCALAR_KEYS = ("distance", "threshold_similarity", "margin")


def _rescale(factor: int) -> None:
    op.execute(
        f"UPDATE applications SET similarity_score = similarity_score * {factor} "  # noqa: S608
        "WHERE similarity_score IS NOT NULL"
    )
    op.execute(
        f"UPDATE application_scores SET value = value * {factor} "  # noqa: S608
        "WHERE score_type = 'similarity'"
    )
    for key in _BREAKDOWN_SCALAR_KEYS:
        op.execute(
            "UPDATE application_scores "  # noqa: S608
            f"SET breakdown = jsonb_set(breakdown, '{{{key}}}', "
            f"to_jsonb(round((breakdown->>'{key}')::numeric * {factor}, 4))) "
            f"WHERE score_type = 'similarity' AND breakdown ? '{key}'"
        )


def upgrade() -> None:
    _rescale(100)


def downgrade() -> None:
    op.execute(
        "UPDATE applications SET similarity_score = similarity_score / 100 "
        "WHERE similarity_score IS NOT NULL"
    )
    op.execute(
        "UPDATE application_scores SET value = value / 100 WHERE score_type = 'similarity'"
    )
    for key in _BREAKDOWN_SCALAR_KEYS:
        op.execute(
            "UPDATE application_scores "  # noqa: S608
            f"SET breakdown = jsonb_set(breakdown, '{{{key}}}', "
            f"to_jsonb(round((breakdown->>'{key}')::numeric / 100, 4))) "
            f"WHERE score_type = 'similarity' AND breakdown ? '{key}'"
        )
