#!/bin/sh
# Single image, two roles. Railway runs this ENTRYPOINT with no args and
# selects the role via SERVICE_ROLE; docker-compose / `railway run` can pass an
# explicit command which takes precedence.
set -e

# Explicit command override (docker-compose `command:`, one-off `railway run …`).
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

if [ "$SERVICE_ROLE" = "worker" ]; then
  exec celery -A src.workers.celery_app worker --loglevel=info -Q fast,slow
fi

# Default / SERVICE_ROLE=api: bring the schema to head, then serve.
# Single-replica deploys only — if the API is scaled out, move this migrate
# step to a Railway preDeployCommand so replicas don't race on the same DB.
alembic upgrade head
exec uvicorn src.main:app --host 0.0.0.0 --port "${PORT:-8000}"
