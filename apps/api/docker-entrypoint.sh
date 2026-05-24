#!/usr/bin/env bash
# Selects the process type for the shared API image (doc 18 §6.1).
# Usage: docker-entrypoint.sh <api|worker_ingest|worker_extract|worker_score|worker_notify|scheduler|migrate|seed|seed-e2e|load-entities|init>
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
    # Uses DATABASE_DIRECT_URL (bypasses PgBouncer; doc 06 §4).
    exec alembic upgrade head
    ;;
  seed)
    # One-shot: idempotently seed a demo workspace + synthetic signals. TODO A2.
    exec python -m civicsignals_api.scripts.seed_demo
    ;;
  seed-e2e)
    # One-shot: idempotently seed the e2e routing scenarios by running the REAL
    # extraction pipeline (no network, no Celery) with the deterministic fake LLM
    # backend, then create the e2e users/workspaces/ICPs and score every signal.
    # Set LLM_BACKEND=fake so the api/workers boot without any API key.
    exec python -m civicsignals_api.scripts.seed_e2e
    ;;
  load-entities)
    # One-shot: load the entity directory (the account universe) from the
    # foundational public-domain datasets — NCES CCD, IPEDS HD, Census of
    # Governments (C1; doc 16 §4/§5/§6). Idempotent (UPSERT on natural key).
    # Extra args after the process type are forwarded to the loader CLI, e.g.
    #   docker compose run --rm api load-entities --source all
    #   docker compose run --rm api load-entities --source nces --path-or-url /data/ccd_lea.csv
    # With no --path-or-url it loads the committed sample fixtures (offline-safe).
    # Run the REAL datasets in production so the directory / ICP matching isn't
    # near-empty (see docs/self-host/production-checklist.md).
    shift || true
    exec python -m civicsignals_api.scripts.load_entities "$@"
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
    # Write beat's schedule DB to a writable path: the process runs as non-root
    # appuser and /app is a root-owned bind mount in dev (Errno 13 otherwise).
    exec celery -A "$CELERY_APP" beat --schedule "${CELERYBEAT_SCHEDULE:-/tmp/celerybeat-schedule}"
    ;;
  init)
    # One-shot init for self-hosted deployments (TODO O2):
    #   1. Run Alembic migrations (direct DB URL, bypasses PgBouncer).
    #   2. Seed the initial admin user + workspace from env vars.
    # Re-running is safe: Alembic is idempotent; seed_admin detects existing rows.
    # Set CIVICSIGNALS_ADMIN_EMAIL and CIVICSIGNALS_ADMIN_PASSWORD in your .env.
    echo "init: running Alembic migrations..."
    alembic upgrade head
    echo "init: seeding admin user and workspace..."
    exec python -m civicsignals_api.scripts.seed_admin
    ;;
  *)
    echo "Unknown process type: $PROC" >&2
    exit 64
    ;;
esac
