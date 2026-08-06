"""Dashboard-derived enumerations and pipeline-stage groupings.

These are *presentation* closed sets the dashboard endpoints compute on the
fly — they are never persisted, but they are part of the API contract, so
they live here (no inline literals in the service) and round-trip to the FE
``ENUMS.md`` / types.

* :class:`JobHealth` — the Performance table's health verdict per job.
* :class:`PipelineBucket` — the four columns of the Candidate Pipeline panel.
  The pipeline has five raw :class:`~src.enums.applications.ApplicationStage`
  values; :data:`STAGE_TO_BUCKET` collapses them into the four FE buckets
  (``hard_filter`` folds into ``sourcing`` alongside ``vector_screen``).
* :data:`SHORTLISTED_STAGES` — the stage(s) that count a candidate as
  "shortlisted" for the health verdict, the Performance ``shortlisted`` count,
  and the ``final_shortlist`` pipeline bucket. A candidate is shortlisted once
  their application reaches the terminal ``done`` stage.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from src.enums.applications import ApplicationStage


class JobHealth(StrEnum):
    """Dashboard health verdict for a job in the Performance table.

    Purely age-based on how long an **OPEN** job has been open, counted in
    **working days** (Mon-Fri). Precedence (first match wins):

    * ``UNHEALTHY`` — OPEN and open **more than**
      ``THRESHOLDS.JOB_UNHEALTHY_WORKING_DAYS`` working days. The badly
      stalled-requisition signal.
    * ``AT_RISK`` — OPEN and open **at least**
      ``THRESHOLDS.JOB_AT_RISK_WORKING_DAYS`` working days (but not yet
      unhealthy). The early-warning signal.
    * ``HEALTHY`` — everything else: a young open job, or any job that isn't
      OPEN (closed / inactive / archived), regardless of age.
    """

    HEALTHY = "healthy"
    AT_RISK = "at_risk"
    UNHEALTHY = "unhealthy"


class PipelineBucket(StrEnum):
    """The four columns of the dashboard's Candidate Pipeline funnel."""

    SOURCING = "sourcing"
    SCREENING = "screening"
    INTERVIEW = "interview"
    FINAL_SHORTLIST = "final_shortlist"


# Collapse the five raw pipeline stages into the four FE buckets. Every
# ApplicationStage MUST appear exactly once so the pipeline aggregation can
# total any application by its stage without a missing-key guard.
STAGE_TO_BUCKET: Final[dict[ApplicationStage, PipelineBucket]] = {
    ApplicationStage.VECTOR_SCREEN: PipelineBucket.SOURCING,
    ApplicationStage.HARD_FILTER: PipelineBucket.SOURCING,
    ApplicationStage.WHATSAPP: PipelineBucket.SCREENING,
    ApplicationStage.INTERVIEW: PipelineBucket.INTERVIEW,
    ApplicationStage.DONE: PipelineBucket.FINAL_SHORTLIST,
}

# Stages that mark a candidate as "shortlisted". The terminal ``done`` stage
# is reached once HR has carried the candidate through the full pipeline.
SHORTLISTED_STAGES: Final[frozenset[ApplicationStage]] = frozenset({ApplicationStage.DONE})
