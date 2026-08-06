"""Make candidate email/phone individually optional (at least one present).

The HR bulk uploader now accepts a CV that yields only one contact field
(name + email, or name + phone) instead of demanding all three. The
candidate identity model degrades to match: ``email`` and ``phone_e164``
become nullable, and the single composite UNIQUE
(``uq_candidates_email_phone``) is replaced by three *partial* unique
indexes so dedup keys on whichever field(s) a candidate actually has:

  - both present : UNIQUE (LOWER(email), phone_e164)
  - email only   : UNIQUE (LOWER(email))      WHERE phone_e164 IS NULL
  - phone only   : UNIQUE (phone_e164)        WHERE email      IS NULL

A row with both fields NULL is never written (the service rejects it as
``parse_failed_no_contact``), so no index covers that case.

Revision ID: 0013_candidate_nullable_contact
Revises: 0012_audit_logs
Create Date: 2026-06-08
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0013_candidate_nullable_contact"
down_revision: str | None = "0012_audit_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("candidates", "email", nullable=True)
    op.alter_column("candidates", "phone_e164", nullable=True)

    # Swap the single composite UNIQUE for three partial ones. The
    # both-present index keeps the original name so existing behaviour
    # (full email+phone dedup) is unchanged; the two single-field indexes
    # only bite when the other field is NULL.
    op.execute("DROP INDEX IF EXISTS uq_candidates_email_phone")
    op.execute(
        "CREATE UNIQUE INDEX uq_candidates_email_phone "
        "ON candidates ((LOWER(email)), phone_e164) "
        "WHERE email IS NOT NULL AND phone_e164 IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_candidates_email_only "
        "ON candidates ((LOWER(email))) "
        "WHERE email IS NOT NULL AND phone_e164 IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_candidates_phone_only "
        "ON candidates (phone_e164) "
        "WHERE phone_e164 IS NOT NULL AND email IS NULL"
    )


def downgrade() -> None:
    # Drop the partial indexes and restore the original full-coverage
    # composite UNIQUE. NOT NULL is only re-applied if no partial-contact
    # rows exist; otherwise the alter_column raises (expected — those rows
    # violate the old invariant and must be resolved by hand first).
    op.execute("DROP INDEX IF EXISTS uq_candidates_phone_only")
    op.execute("DROP INDEX IF EXISTS uq_candidates_email_only")
    op.execute("DROP INDEX IF EXISTS uq_candidates_email_phone")
    op.execute(
        "CREATE UNIQUE INDEX uq_candidates_email_phone ON candidates ((LOWER(email)), phone_e164)"
    )
    op.alter_column("candidates", "phone_e164", nullable=False)
    op.alter_column("candidates", "email", nullable=False)
