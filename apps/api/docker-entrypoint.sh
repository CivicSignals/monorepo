#!/usr/bin/env bash
# Selects the process type for the shared API image (doc 18 §6.1).
# Usage: docker-entrypoint.sh <api|worker_ingest|worker_extract|worker_score|worker_notify|scheduler>
set -euo pipefail

PROC="${1:-api}"
CELERY_APP="civicsignals_api.celery_app"

case "$PROC" in
  api)
    exec uv run uvicorn civicsignals_api.main:app --host 0.0.0.0 --port 8000
    ;;
  worker_ingest)
    exec uv run celery -A "$CELERY_APP" worker -Q ingest -n ingest@%h
    ;;
  worker_extract)
    exec uv run celery -A "$CELERY_APP" worker -Q extract -n extract@%h
    ;;
  worker_score)
    exec uv run celery -A "$CELERY_APP" worker -Q score -n score@%h
    ;;
  worker_notify)
    exec uv run celery -A "$CELERY_APP" worker -Q notify -n notify@%h
    ;;
  scheduler)
    # Leader-elected singleton (doc 18 §6.1). Redis lock arrives with D14.
    exec uv run celery -A "$CELERY_APP" beat
    ;;
  *)
    echo "Unknown process type: $PROC" >&2
    exit 64
    ;;
esac
