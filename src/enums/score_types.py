"""Score-type enumeration for the ``application_scores`` audit table.

Each ``ApplicationScore`` row records one *attempt* at computing one of
the three score families. ``score_type`` is the family; ``value`` is the
numeric outcome; ``breakdown`` is the per-signal detail. Append-only:
a re-score creates a new row, never updates an existing one.

Why a typed enum instead of free-form strings? The architecture doc's
schema column comment ("``'similarity' / 'hard_filter' / 'authenticity'``")
implies a closed set. Per the project-wide "no hardcoded values"
directive, both the writer (Step 3.9 similarity, Step 4.3 hard_filter,
Step 3.7 authenticity) and the reader (the detail-endpoint serialiser)
reference these via the enum.
"""

from __future__ import annotations

from enum import StrEnum


class ScoreType(StrEnum):
    """The three score families recorded in ``application_scores``.

    * ``SIMILARITY`` — cosine similarity between CV and JD embeddings
      (Step 3.9 ``score_similarity_task``).
    * ``HARD_FILTER`` — Claude Opus's 5-signal hard-filter aggregate
      (the "CV score": skills / experience / industry / regional /
      role-seniority, weighted 35/35/10/10/10) via
      ``score_hard_filters_task``.
    * ``AUTHENTICITY`` — the candidate's authenticity blend
      (Step 3.7 — currently denormalised on the Candidate row; Step 4.x
      may surface it on application_scores as well).
    """

    SIMILARITY = "similarity"
    HARD_FILTER = "hard_filter"
    AUTHENTICITY = "authenticity"


class ScoreModel(StrEnum):
    """Identifier for the model / mechanism that produced the score.

    Open-ended in the database (``varchar``) so future providers don't
    need a migration; this enum captures the values today's pipeline
    actually writes. The string suffices to disambiguate later when
    HR audits "which model rated this CV".
    """

    CLAUDE_OPUS = "claude-opus"
    CLAUDE_SONNET = "claude-sonnet"
    CLAUDE_HAIKU = "claude-haiku"
    OPENAI_EMBED_3_SMALL = "openai-text-embedding-3-small"
    DETERMINISTIC = "deterministic"
    # The authenticity score is an aggregate of signals computed by *different*
    # mechanisms (some by Claude, some deterministic), so no single model owns
    # it. Per-signal provenance lives in each signal's ``computed_by`` field.
    BLENDED = "blended"
