"""Tuning thresholds used across scoring pipelines.

Every numeric constant the doc treats as "tuning" lives here so it can be
adjusted in one place. Runtime code in services / agents MUST import from
this module rather than hard-coding numbers.

Where a threshold can usefully be overridden per environment (e.g. lowering
the embedding-rejection threshold in staging), that knob lives in
``src/config.py`` as a ``Settings`` field whose default matches the value
below. Services read from ``settings.<field>``, not from this module, when
an override path exists.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from src.enums.authenticity import AuthenticityBand
from src.enums.authenticity_signals import SignalKey

# --- Embedding pre-filter ----------------------------------------------------

# Cosine *distance* (on the 0-100 percentage scale similarity scores use)
# above which a CV is auto-rejected from a job — the relevancy pre-filter
# that runs before the (manual) Opus CV-score stage. 60 distance == 40
# similarity: a CV is rejected when its similarity to the JD falls below
# 40%. Mirrors ``Settings.similarity_rejection_threshold``.
SIMILARITY_REJECTION_THRESHOLD: Final[float] = 60.0

# Minimum cosine *similarity* (0-100) for a pooled candidate to be considered
# relevant to a job in talent-pool **search** (``GET /talent-pool/search?job_id``).
# Stricter than the ingestion floor above: full-text CV-vs-JD embeddings give any
# two tech profiles a baseline overlap, so a 40-similarity match (the ingestion
# auto-reject floor) is too weak to count as a *search* hit — it lets, e.g., a
# Software Engineer surface for a Senior QA job. 50 keeps genuinely related
# candidates while dropping weak cross-role matches.
TALENT_POOL_SEARCH_MIN_SIMILARITY: Final[float] = 50.0


# --- Authenticity banding (Step 3.7) ----------------------------------------

# Floor (inclusive) for each band on a 0-100 authenticity score:
#   score >= AUTHENTIC floor             → AUTHENTIC
#   REVIEW floor <= score < AUTHENTIC    → REVIEW
#   score < REVIEW floor                 → FABRICATED
#
# Bounds per architecture doc Step 3.7 acceptance: "Band assignment matches
# thresholds (75/50)". Tune by editing these — do not inline literals in
# scoring code.
AUTHENTICITY_BAND_THRESHOLDS: Final[Mapping[AuthenticityBand, float]] = {
    AuthenticityBand.AUTHENTIC: 75.0,
    AuthenticityBand.REVIEW: 50.0,
    AuthenticityBand.FABRICATED: 0.0,
}


def band_for_score(score: float) -> AuthenticityBand:
    """Map a 0-100 authenticity score to its band."""
    if score >= AUTHENTICITY_BAND_THRESHOLDS[AuthenticityBand.AUTHENTIC]:
        return AuthenticityBand.AUTHENTIC
    if score >= AUTHENTICITY_BAND_THRESHOLDS[AuthenticityBand.REVIEW]:
        return AuthenticityBand.REVIEW
    return AuthenticityBand.FABRICATED


# --- Authenticity aggregation weights ---------------------------------------
# The full six-signal weighting (sums to 1.00), applied to the job-aware,
# application-level score:
#
#     Achievement Specificity      0.25
#     Skill Timeline Coherence     0.20
#     Internal Consistency         0.20
#     Linguistic Genericity        0.15
#     Structural Templating        0.10
#     JD Keyword Mirroring         0.10
#
# The candidate-level *baseline* (job-agnostic) score reuses this same dict
# over the five non-mirroring signals: the aggregator normalises by the sum
# of the weights actually present, so the five-signal subset divides by 0.90
# and lands back on the 0-100 scale with the doc's original ratios intact
# (0.25/0.90 ≈ 27.8, etc.). One dict, both scores.
AUTHENTICITY_WEIGHTS: Final[Mapping[SignalKey, float]] = {
    SignalKey.SPECIFICITY: 0.25,
    SignalKey.TIMELINE_COHERENCE: 0.20,
    SignalKey.CONSISTENCY: 0.20,
    SignalKey.LINGUISTIC_GENERICITY: 0.15,
    SignalKey.STRUCTURAL_TEMPLATING: 0.10,
    SignalKey.JD_KEYWORD_MIRRORING: 0.10,
}

# A signal whose score is below this bar is flagged as a "concern" (the
# ``concern`` boolean on each signal + membership in ``top_concerns``). Mirrors
# the source prompt spec: concern == score < 60.
AUTHENTICITY_CONCERN_THRESHOLD: Final[float] = 60.0

# Cap on how many signals ``top_concerns`` enumerates (lowest-scoring first).
AUTHENTICITY_TOP_CONCERNS_MAX: Final[int] = 3


# --- Dashboard performance / health -----------------------------------------

# How long an OPEN job may stay open before its dashboard health degrades, in
# **working days** (Mon-Fri; weekends excluded). Health is purely age-based now:
#   working_days_open >= AT_RISK  → AT_RISK   (18, 19, 20)
#   working_days_open  > UNHEALTHY → UNHEALTHY (21+)
#   otherwise                      → HEALTHY   (young open jobs, and any
#                                               non-open job regardless of age)
# UNHEALTHY must be >= AT_RISK. Edit here — never inline the numbers in the
# dashboard service.
JOB_AT_RISK_WORKING_DAYS: Final[int] = 18
JOB_UNHEALTHY_WORKING_DAYS: Final[int] = 20

# Days an application can sit at the ``interview`` stage (status active)
# without advancing before it surfaces in the dashboard's "pending feedback"
# list — the post-interview decision is overdue. Measured from
# ``stage_updated_at`` (when the app entered the interview stage).
INTERVIEW_PENDING_FEEDBACK_DAYS: Final[int] = 3

# How many of the nearest upcoming interviews the dashboard preview returns by
# default (the "top 3" card). The "show all" view requests a larger ``limit``.
DASHBOARD_UPCOMING_INTERVIEWS_PREVIEW: Final[int] = 3

# Hard cap on the upcoming-interviews ``limit`` query param so the "show all"
# view can't ask for an unbounded result set.
DASHBOARD_UPCOMING_INTERVIEWS_MAX: Final[int] = 200


# --- Interview scoring (HR-entered) -----------------------------------------

# Bounds for the manual interview mark HR records after the interview. Enforced
# at the schema boundary so an out-of-range value is a clean 422, not a bad row.
# Edit here — never inline the range in the schema/service.
INTERVIEW_SCORE_MIN: Final[int] = 0
INTERVIEW_SCORE_MAX: Final[int] = 100

# Max length of the free-text interview comment stored alongside the mark.
INTERVIEW_COMMENT_MAX_LENGTH: Final[int] = 2000
