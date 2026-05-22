# CivicSignals API image. One image, six process types (doc 18 §6.1) selected
# by the entrypoint argument. Multi-arch + cosign-signing handled in CI (TODO O1).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# ---- dependency layer (cached on lockfile) ----
COPY pyproject.toml ./
# uv.lock is committed once `uv lock` has been run; copy if present.
COPY uv.loc[k] ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev || uv sync --no-install-project

# ---- app layer ----
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev || uv sync

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["api"]
