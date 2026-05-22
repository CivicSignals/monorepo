# CivicSignals API image — one image, six process types (doc 18 §6.1).
# Process type is selected at runtime by docker-entrypoint.sh:
#   api | worker_ingest | worker_extract | worker_score | worker_notify | scheduler
#
# Build args:
#   EXTRAS  — comma-separated optional extras to install (default: "")
#             Use "ingestion,extraction" for worker images that need heavy deps.
#
# Multi-arch (linux/amd64 + linux/arm64) and cosign-signing are handled in CI
# (.github/workflows/release-images.yml — task O1).
# SPDX-License-Identifier: AGPL-3.0-only

# ---- base: Python 3.12 + uv from the official uv image ----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# ---- deps stage: install locked core dependencies into a venv ----
# Separate from the app layer so layer cache survives app code changes.
FROM base AS deps

ARG EXTRAS=""

COPY pyproject.toml ./
# uv.lock committed by `uv lock`; glob pattern avoids hard failure when absent.
COPY uv.loc[k] ./

RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$EXTRAS" ]; then \
        uv sync --no-install-project --no-dev --extra "$EXTRAS" || \
        uv sync --no-install-project --no-dev; \
    else \
        uv sync --no-install-project --no-dev || \
        uv sync --no-install-project; \
    fi

# ---- builder stage: copy source and install the project itself ----
FROM deps AS builder

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$EXTRAS" ]; then \
        uv sync --no-dev --extra "$EXTRAS" || uv sync --no-dev; \
    else \
        uv sync --no-dev || uv sync; \
    fi

# ---- final (lean) runtime image ----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS final

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Use the venv Python directly so we don't need uv's cache at runtime.
    # The venv is at /app/.venv and was compiled in the builder stage.
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV="/app/.venv"

# Non-root user for the final stage.
# Create a home directory so that tools that need one (e.g. uv run) work,
# but note we prefer calling venv Python directly (see entrypoint).
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --create-home --shell /sbin/nologin appuser

WORKDIR /app

# Copy the installed virtual environment and application source from builder.
COPY --from=builder --chown=appuser:appgroup /app /app

# Ensure entrypoint is executable.
RUN chmod +x /app/docker-entrypoint.sh

USER appuser

EXPOSE 8000

# Healthcheck: poll the /healthz endpoint every 30 s.
# Start period gives uvicorn time to come up before the first check fires.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" \
    || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["api"]
