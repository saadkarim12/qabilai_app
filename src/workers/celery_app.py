"""Celery application instance for Kabil.ai background work.

Two queues live on the same Redis broker:

* ``fast`` — short, latency-sensitive jobs (reserved for future steps).
* ``slow`` — embedding generation, Claude calls, anything that can take
  several seconds. Step 2.6's job-pipeline tasks land here.

Workers are started with ``-Q fast,slow`` in dev to consume both;
production typically runs separate worker processes per queue so a
backlog on one doesn't starve the other.

The Redis connection used by the broker / result backend is configured
independently of the app's main Redis (``REDIS_URL``, db 0) and AI
caches so a backlog of tasks can't crowd cache evictions.
"""

from __future__ import annotations

from typing import Final

from celery import Celery

from src.config import settings

FAST_QUEUE: Final[str] = "fast"
SLOW_QUEUE: Final[str] = "slow"

# Default `acks_late=True` so a task that crashes mid-execution gets
# re-delivered to another worker. Tasks must be idempotent — the pipeline
# helpers in ``tasks/job_pipeline.py`` already guarantee this.
celery_app: Final[Celery] = Celery(
    "kabil",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "src.workers.tasks.job_pipeline",
        "src.workers.tasks.cv_pipeline",
        "src.workers.tasks.scoring",
        "src.workers.tasks.whatsapp",
        "src.workers.tasks.interviews",
    ],
)

celery_app.conf.update(
    task_default_queue=SLOW_QUEUE,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Eager mode runs tasks synchronously in the calling process. We flip
    # this on in tests so .apply_async() executes immediately; production
    # leaves it off and dispatches over the broker.
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    # Disable result expiry for now — we don't read .get() anywhere yet
    # and unbounded growth is unlikely with two task types per job.
    result_expires=None,
    # Single connection-pool retry on broker startup, otherwise raise so
    # the API logs a clear error rather than hanging on send.
    broker_connection_retry_on_startup=True,
)
