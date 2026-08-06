"""CV-processing pipeline enumerations (Step 3.9).

Single source of truth for the keys and state values that appear in
``applications.pipeline_status`` JSONB. Mirrors :mod:`src.enums.pipeline`
(which serves the *job* pipeline) so the two surfaces stay symmetrical
and any future "show pipeline progress" UI doesn't need two code paths.

Example ``pipeline_status`` shape on an Application row:

.. code-block:: json

    {
      "extract_text": "ok",
      "parse_cv": "ok",
      "authenticity": "ok",
      "embed_cv": "ok",
      "similarity": "ok",
      "auto_reject": "skipped"
    }
"""

from __future__ import annotations

from enum import StrEnum


class CvPipelineStep(StrEnum):
    """One step in the CV processing chain.

    Order matches the chain composition in :mod:`src.workers.tasks.cv_pipeline`
    (extract -> parse -> authenticity -> embed -> similarity -> auto_reject).
    Adding a step is an enum edit plus a new task; downstream readers
    iterate the enum so a missing key just renders as "not run yet".
    """

    EXTRACT_TEXT = "extract_text"
    PARSE_CV = "parse_cv"
    AUTHENTICITY = "authenticity"
    EMBED_CV = "embed_cv"
    SIMILARITY = "similarity"
    AUTO_REJECT = "auto_reject"


class CvPipelineStepState(StrEnum):
    """Per-step status stored in ``applications.pipeline_status``.

    A missing key means "never run". ``OK`` means the step finished
    successfully and any target columns are populated. ``FAILED`` means
    the step finished with a non-transient error (message in
    ``{step}_error``). ``PENDING`` is written when a retry-eligible task
    is in flight. ``SKIPPED`` is used by the auto-reject step when the
    similarity score is *above* the rejection threshold (no rejection
    necessary, but the step still ran) -- distinguishing this from
    ``OK`` keeps the UI honest about what actually happened.
    """

    PENDING = "pending"
    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"
