"""Scoring services.

Each sub-module owns one score-family:

* :mod:`src.services.scoring.hard_filter_service` — Step 4.3 Sonnet
  scoring against the 5-signal rubric.

The Celery task wrappers live in :mod:`src.workers.tasks.scoring`.
"""

from __future__ import annotations
