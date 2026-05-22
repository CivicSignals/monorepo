#!/usr/bin/env bash
# Phase 0 idempotent deploy script — task A4 (doc 06 §10).
#
# Invoked by GitHub Actions (.github/workflows/deploy.yml) on merge to main.
# Can also be run manually from any machine that has SSH access to the VPS.
#
# Usage:
#   DEPLOY_HOST=civicsignals@1.2.3.4 \
#   DEPLOY_DIR=/opt/civicsignals \
#   CIVIC_VERSION=sha-abc1234 \
#   bash infra/deploy/deploy.sh
#
# Environment variables (all have defaults; DEPLOY_HOST is required):
#   DEPLOY_HOST     — user@host (required, e.g. civicsignals@1.2.3.4)
#   DEPLOY_DIR      — absolute path on the VPS  (default: /opt/civicsignals)
#   CIVIC_VERSION   — image tag to deploy        (default: latest)
#   SSH_OPTS        — extra SSH flags            (default: -o StrictHostKeyChecking=no)
#   HEALTH_URL      — URL to probe after deploy  (default: http://localhost/healthz)
#                     NOTE: if nginx TLS is active and /healthz on port 80 is
#                     proxied directly (not redirected), this probe works without
#                     curl needing to follow 301s. See infra/provision/provision.sh.
#   HEALTH_RETRIES  — number of health-check attempts before rollback (default: 12)
#   HEALTH_INTERVAL — seconds between attempts   (default: 5)
#
# Exit codes:
#   0 — deploy succeeded (or no-op if already on this version)
#   1 — deploy failed; rollback attempted
#
# SPDX-License-Identifier: AGPL-3.0-only
set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
: "${DEPLOY_HOST:?DEPLOY_HOST is required (e.g. civicsignals@1.2.3.4)}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/civicsignals}"
CIVIC_VERSION="${CIVIC_VERSION:-latest}"
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=no -o BatchMode=yes}"
HEALTH_URL="${HEALTH_URL:-http://localhost/healthz}"
HEALTH_RETRIES="${HEALTH_RETRIES:-12}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-5}"

COMPOSE_FILE="infra/docker-compose.yml"
ENV_FILE="infra/.env"

log() { echo "[deploy] $*"; }
err() { echo "[deploy:ERROR] $*" >&2; }

# ---------------------------------------------------------------------------
# Remote deploy script (heredoc; executed on the VPS via SSH)
# All config is passed as positional args to avoid SSH env-var leakage.
# ---------------------------------------------------------------------------
# shellcheck disable=SC2087
ssh ${SSH_OPTS} "${DEPLOY_HOST}" bash -s -- \
  "${DEPLOY_DIR}" "${CIVIC_VERSION}" "${COMPOSE_FILE}" "${ENV_FILE}" \
  "${HEALTH_URL}" "${HEALTH_RETRIES}" "${HEALTH_INTERVAL}" << 'REMOTE'
set -euo pipefail

DEPLOY_DIR="$1"
CIVIC_VERSION="$2"
COMPOSE_FILE="$3"
ENV_FILE="$4"
HEALTH_URL="$5"
HEALTH_RETRIES="$6"
HEALTH_INTERVAL="$7"

log()  { echo "[deploy] $*"; }
err()  { echo "[deploy:ERROR] $*" >&2; }
fail() { err "$*"; exit 1; }

cd "${DEPLOY_DIR}" || fail "DEPLOY_DIR '${DEPLOY_DIR}' not found. Run infra/provision/provision.sh first."

COMPOSE="docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE}"

# ---- 0. Record currently-running tag for rollback -------------------------
# Extract the tag portion only (after the last ':') from the running api container.
# Fallback to "unknown" if the container is not running or inspection fails.
PREVIOUS_TAG="unknown"
if docker inspect civicsignals-api-1 >/dev/null 2>&1; then
  RUNNING_IMAGE=$(docker inspect --format '{{.Config.Image}}' civicsignals-api-1 2>/dev/null || true)
  if [ -n "${RUNNING_IMAGE}" ]; then
    PREVIOUS_TAG="${RUNNING_IMAGE##*:}"   # strip everything up to and including the last ':'
  fi
fi
log "Currently running api tag: ${PREVIOUS_TAG}"

# ---- 1. Pull new images -----------------------------------------------
log "Pulling CIVIC_VERSION=${CIVIC_VERSION} ..."
export CIVIC_VERSION
${COMPOSE} pull --quiet
log "Pull complete."

# ---- 2. Run migrations (Alembic, direct DB connection) ----------------
# Runs before swapping containers so the schema is always ahead-or-equal of the
# running code (expand/contract pattern; doc 06 §10).
# The init container reads DATABASE_DIRECT_URL from the compose --env-file;
# we do NOT override it here — alembic/env.py already prefers DATABASE_DIRECT_URL.
log "Running Alembic migrations ..."
${COMPOSE} run --rm init
log "Migrations complete."

# ---- 3. Bring up new containers (rolling restart) ---------------------
log "Bringing up new containers ..."
${COMPOSE} up -d --remove-orphans --no-build
log "Containers restarted."

# ---- 4. Health check --------------------------------------------------
log "Waiting for health at ${HEALTH_URL} (up to $((HEALTH_RETRIES * HEALTH_INTERVAL))s) ..."
attempt=0
until curl -sf --max-time 5 "${HEALTH_URL}" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "${attempt}" -ge "${HEALTH_RETRIES}" ]; then
    err "Health check failed after ${attempt} attempts."
    # ---- 5. Rollback on failure ----------------------------------------
    if [ "${PREVIOUS_TAG}" != "unknown" ]; then
      log "Rolling back to tag: ${PREVIOUS_TAG} ..."
      CIVIC_VERSION="${PREVIOUS_TAG}" ${COMPOSE} pull --quiet || true
      CIVIC_VERSION="${PREVIOUS_TAG}" ${COMPOSE} up -d --remove-orphans --no-build || true
      log "Rollback applied. Investigate logs before re-deploying."
    else
      log "No previous tag recorded — manual recovery required."
    fi
    exit 1
  fi
  log "  attempt ${attempt}/${HEALTH_RETRIES} — not yet healthy, retrying in ${HEALTH_INTERVAL}s ..."
  sleep "${HEALTH_INTERVAL}"
done
log "Service healthy."

# ---- 6. Prune dangling images (save disk on small VPS) ----------------
docker image prune -f --filter "until=24h" >/dev/null 2>&1 || true
log "Image prune done."

log "Deploy of CIVIC_VERSION=${CIVIC_VERSION} complete."
REMOTE

log "Remote deploy finished."
