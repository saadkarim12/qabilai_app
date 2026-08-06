"""Authenticity scoring enumerations.

The score (0-100) and band are both produced by the Phase 4 authenticity
pipeline. Band values are stored as strings in the ``candidates`` table so
manual inspection of a row makes the meaning obvious — see
``src/db/models/candidate.py``.
"""

from __future__ import annotations

from enum import StrEnum


class AuthenticityBand(StrEnum):
    """Coarse-grained classification of an authenticity score.

    Thresholds for binning a numeric score into a band live with the
    scoring service (Phase 4.x), not here — this enum is purely the set
    of legal values that may appear on ``candidates.authenticity_band``.
    """

    AUTHENTIC = "authentic"
    REVIEW = "review"
    FABRICATED = "fabricated"
