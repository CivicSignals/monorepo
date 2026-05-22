#!/usr/bin/env bash
# Phase 0 deploy: SSH to the VPS, pull new images, run migrations, restart.
# Invoked by GitHub Actions on merge to main (TODO A4). Idempotent.
set -euo pipefail

: "${DEPLOY_HOST:?set DEPLOY_HOST=user@host}"
: "${DEPLOY_DIR:=/opt/civicsignals}"
CIVIC_VERSION="${CIVIC_VERSION:-latest}"

echo "Deploying CivicSignals ${CIVIC_VERSION} to ${DEPLOY_HOST}:${DEPLOY_DIR}"

ssh "${DEPLOY_HOST}" bash -s <<EOF
set -euo pipefail
cd "${DEPLOY_DIR}"
export CIVIC_VERSION="${CIVIC_VERSION}"

docker compose pull
# Apply migrations before swapping app containers (Alembic; doc 06 §10).
docker compose run --rm api uv run alembic upgrade head
docker compose up -d --remove-orphans

docker image prune -f
EOF

echo "Deploy complete."
