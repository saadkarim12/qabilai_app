"""Rescale persisted hard-filter signal weights from fractions to percentages.

Hard-filter signal weights moved from fractions summing to 1.0 (0.35, 0.10)
to percentages out of 100 (35, 10); the aggregator now divides the weighted
sum by the total weight, so signal/total *scores* are unchanged. Only the
``weight`` field embedded in the breakdown JSON needs backfilling:

  - ``applications.hard_filter_breakdown``  -> ``{signal: {.., weight}}``
  - ``application_scores.breakdown`` (hard_filter rows)
        -> ``{"signals": {signal: {.., weight}}}``

Each ``weight`` is multiplied by 100. Rows without the key (legacy/empty
breakdowns) are skipped. Similarity/authenticity rows are untouched.

Revision ID: 0015_hard_filter_weight_pct
Revises: 0014_similarity_score_percentage
Create Date: 2026-06-08
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0015_hard_filter_weight_pct"
down_revision: str | None = "0014_similarity_score_percentage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The five hard-filter signal keys (HardFilterKey values).
_SIGNAL_KEYS = ("skills", "experience", "industry", "regional", "role_seniority")


def _rescale(op_sql: str) -> None:
    """``op_sql`` is "* 100" (upgrade) or "/ 100" (downgrade)."""
    for key in _SIGNAL_KEYS:
        # applications.hard_filter_breakdown -> {<key>: {weight}}
        op.execute(
            "UPDATE applications "  # noqa: S608
            f"SET hard_filter_breakdown = jsonb_set(hard_filter_breakdown, '{{{key},weight}}', "
            f"to_jsonb(round((hard_filter_breakdown -> '{key}' ->> 'weight')::numeric {op_sql}, 4))) "
            f"WHERE hard_filter_breakdown -> '{key}' ? 'weight'"
        )
        # application_scores.breakdown -> {"signals": {<key>: {weight}}}
        op.execute(
            "UPDATE application_scores "  # noqa: S608
            f"SET breakdown = jsonb_set(breakdown, '{{signals,{key},weight}}', "
            f"to_jsonb(round((breakdown -> 'signals' -> '{key}' ->> 'weight')::numeric {op_sql}, 4))) "
            f"WHERE score_type = 'hard_filter' AND breakdown -> 'signals' -> '{key}' ? 'weight'"
        )


def upgrade() -> None:
    _rescale("* 100")


def downgrade() -> None:
    _rescale("/ 100")
