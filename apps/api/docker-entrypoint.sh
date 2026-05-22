#!/usr/bin/env bash
# Selects the process type for the shared API image (doc 18 §6.1).
# Usage: docker-entrypoint.sh <api|worker_ingest|worker_extract|worker_score|worker_notify|scheduler|migrate|seed>
#
# In the production container image the venv Python is on PATH (/app/.venv/bin).
# Executables are invoked directly (uvicorn, celery) rather than via `uv run`
# so we don't need a writeable uv cache at runtime.  In local dev the compose
# volume-mounts the source tree and `uv run` is still fine because root's cache
# is available.
set -euo pipefail

PROC="${1:-api}"
CELERY_APP="civicsignals_api.celery_app"

case "$PROC" in
  api)
    exec uvicorn civicsignals_api.main:app --host 0.0.0.0 --port 8000
    ;;
  migrate)
    # One-shot: bring the schema to head, then exit (used as a startup gate in
    # docker-compose so app processes only start once migrated). TODO A2.
    exec alembic upgrade head
    ;;
  seed)
    # One-shot: idempotently seed a demo workspace + synthetic signals. TODO A2.
    exec python -m civicsignals_api.scripts.seed_demo
    ;;
  worker_ingest)
    exec celery -A "$CELERY_APP" worker -Q ingest -n ingest@%h
    ;;
  worker_extract)
    exec celery -A "$CELERY_APP" worker -Q extract -n extract@%h
    ;;
  worker_score)
    exec celery -A "$CELERY_APP" worker -Q score -n score@%h
    ;;
  worker_notify)
    exec celery -A "$CELERY_APP" worker -Q notify -n notify@%h
    ;;
  scheduler)
    # Leader-elected singleton (doc 18 §6.1). Redis lock arrives with D14.
    exec celery -A "$CELERY_APP" beat
    ;;
  *)
    echo "Unknown process type: $PROC" >&2
    exit 64
    ;;
esac
