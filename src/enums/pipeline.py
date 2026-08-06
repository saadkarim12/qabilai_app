"""Pipeline-step enumerations for the job background pipeline (Step 2.6).

These enums are the single source of truth for the keys and state values
that appear in ``Job.pipeline_status`` JSONB. Adding a new step or state
is an enum edit + (for steps) a new task — no string-literal hunt.

Example ``pipeline_status`` shape:

.. code-block:: json

    {
      "embedding": "ok",
      "questions": "failed",
      "questions_error": "Claude returned no questions"
    }
"""

from __future__ import annotations

from enum import StrEnum


class PipelineStep(StrEnum):
    """Pipeline tasks that run on a ``draft → open`` transition."""

    EMBEDDING = "embedding"
    QUESTIONS = "questions"


class PipelineStepState(StrEnum):
    """Per-step status stored in ``pipeline_status``.

    A missing key means "never run". ``OK`` means the step completed and
    the target column is populated; ``FAILED`` means the step finished
    with a non-transient error (its message is in ``{step}_error``);
    ``PENDING`` is set while a retry-eligible task is in flight.
    """

    PENDING = "pending"
    OK = "ok"
    FAILED = "failed"
