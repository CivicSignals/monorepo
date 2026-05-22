# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read the spec first (it is gitignored, not absent)

The authoritative product/engineering spec lives in numbered Markdown files at the repo root — `01-product-brief.md` … `19-signal-processing.md`, plus `README.md`, `TODO.md`, and `wireframes.html`. **These are intentionally listed in `.gitignore`** (root-anchored), so they exist on disk but never get committed. They are *not* clutter — they are the design of record. Before non-trivial work, read the relevant doc:

- **doc 06** (`06-architecture.md`) — modulith, modules, process model, DB, deployment. The single most important file.
- **doc 07** data model · **doc 08** API spec · **doc 14** signal matching/scoring · **doc 18** ingestion · **doc 19** signal processing.
- **`TODO.md`** — the live MVP V1 worklist (see below).

Do not "fix" the untracked docs by committing them. The root `README.md` is gitignored too, so the committed repo has no top-level README — only subfolder READMEs are tracked.

## Work is organized by task IDs in TODO.md

`TODO.md` drives the build. Every task has a stable ID (epics `A`–`R`, plus `QA-N`, `LC-N`), a `Status` (`NOT_STARTED`/`IN_PROGRESS`/`BLOCKED`/`DONE`), and `Dependencies`. When you implement something: find the matching task ID, update its `Status` in `TODO.md`, and keep the status-summary counts at the bottom of the file in sync. Code comments reference these IDs (e.g. `# TODO E1`) to mark where future work attaches.

Only **A1 (monorepo skeleton) is DONE** — most of the tree is intentionally stubs (docstrings + `pass`) waiting on later tasks.

## Toolchain prerequisites

This is a **Turborepo** monorepo with two language ecosystems:
- **Node 22** (`.nvmrc`) + **pnpm 9** (`corepack enable`) — orchestration and `apps/web`, `packages/sdk-ts`, `packages/recipe-schema`.
- **Python 3.12** (`.python-version`) + **[uv](https://docs.astral.sh/uv/)** — `apps/api`, `packages/sdk-py`.

`pnpm` and `uv` may not be installed yet; install them before running anything. Turborepo task shims (`apps/api/package.json`, `packages/sdk-py/package.json`) just call `uv run …`, so the root `pnpm <task>` commands only work once `uv` is on PATH.

## Commands

```bash
# Install
pnpm install                       # Node workspaces (root)
cd apps/api && uv sync             # Python deps for the API (and again in packages/sdk-py)

# Full local stack (Postgres+pgvector, Redis, MinIO, Mailpit, PgBouncer, api, 4 workers, scheduler, web)
make dev                           # = docker compose -f infra/docker-compose.dev.yml up --build; make env creates .env first
make down                          # stop;  make clean = stop + drop volumes (destroys data)

# Run apps individually (no Docker)
pnpm --filter @civicsignals/web dev                                  # web on :3000
cd apps/api && uv run uvicorn civicsignals_api.main:app --reload --port 8000   # api on :8000; /docs, /api/v1/openapi.json

# Lint / format / types — across everything via Turbo
pnpm lint  |  pnpm format  |  pnpm typecheck
cd apps/api && uv run ruff check .   # ruff = lint+format (replaces black/isort/flake8)
cd apps/api && uv run mypy src       # mypy is strict

# Tests
pnpm test                                            # all workspaces via Turbo
cd apps/api && uv run pytest                         # Python (pytest, asyncio auto-mode, coverage on)
cd apps/api && uv run pytest tests/test_health.py::test_healthz   # a single test
cd apps/api && uv run pytest -k smoke                # by keyword (each module ships tests/test_smoke.py)
pnpm --filter @civicsignals/web test                 # web (vitest)

# Migrations (Alembic; autogenerate sees every module's models)
cd apps/api && uv run alembic revision --autogenerate -m "describe change"
cd apps/api && uv run alembic upgrade head           # or `make migrate` to run inside the api container

# Regenerate the TS SDK types from the live OpenAPI doc (api must be running)
pnpm --filter @civicsignals/sdk-ts generate
```

## Architecture (the parts that span files)

**It's a modulith, not microservices** (doc 06 §1). `apps/api` is one deployable FastAPI app split into bounded modules. Same Python image, **six process types** selected by `apps/api/docker-entrypoint.sh`: `api` (uvicorn) + four Celery workers (`worker_ingest`/`worker_extract`/`worker_score`/`worker_notify`) + `scheduler` (Celery beat, singleton). Compose maps each `command:` to one of these.

**The module pattern is load-bearing.** Each module under `apps/api/src/civicsignals_api/modules/<name>/` (there are 18; see doc 06 §3) exposes exactly: `services.py`, `models.py`, `schemas.py`, `routes.py`, `tasks.py`, `tests/`.
- **No module imports another module's internals.** Cross-module calls go through the target's `services.py` only.
- Each module **owns its tables** (prefixed `<module>_`, e.g. `signals_signal`) and is the only one that migrates them.
- To add a module: create the directory with those files (copy the shape of an existing one), then add its `routes` import to `apps/api/src/civicsignals_api/api/v1.py`, which mounts every module under `/api/v1` (`public_feed` is intentionally left unmounted — it's v2).

**Cross-cutting seams** all live at `apps/api/src/civicsignals_api/`:
- `db.py` — the single declarative `Base` all models import (so Alembic's `env.py` autoloads every `modules/*/models.py`). App connections go through **PgBouncer in transaction mode** (`DATABASE_URL`, port 6432); a **direct** connection (`DATABASE_DIRECT_URL`, 5432) bypasses it for Alembic, `LISTEN/NOTIFY`, and long jobs.
- `celery_app.py` — beat schedule + `task_routes` that map task-name prefixes (`ingestion.*`, `extraction.*`, `signals.*`, …) to the per-process queues. A module's beat task must be registered with an explicit `name="<module>.<func>"`.
- `events.py` — in-process event bus (`signal.created`, etc.). Deliberately not Kafka/NATS; this is the seam to replace if a module is ever extracted to its own service.
- `llm_gateway.py` — **no module calls a vendor SDK directly.** All LLM access (Anthropic/OpenAI/Ollama) routes through the gateway for model selection, per-workspace token accounting, and prompt versioning. Versioned prompts live in `apps/api/prompts/`.

**REST conventions** (doc 06 §5, enforced in both SDKs): versioned `/api/v1`, bearer auth, **cursor** pagination (`?cursor=…&limit=25`, never offset), RFC 7807 `application/problem+json` errors, `X-Workspace-Id` header for workspace scoping.

**Ingestion: connectors vs recipes** (doc 16 §17, doc 18). A **connector** is code for a *source type* (we write ~25–30). A **recipe** is declarative YAML that instantiates a connector for one tenant/source (hundreds–thousands, community-contributable). `packages/recipe-schema` holds the draft-07 JSON Schemas that are the **single source of truth** for both the Python runner and TS tooling. Each recipe ships golden fixtures under `recipes/<id>/fixtures/` (`*.html` + `*.expected.json`) that CI replays. The connector lifecycle is `discover → fetch → extract → normalize`; `extract` uses ordered selector fallbacks (primary → fallback → LLM-assisted → dead-letter), flagging `degraded: true` on fallback.

**Frontend state split** (doc 06 §2): **TanStack Query owns server state** (caching, mutations, optimistic updates); **Zustand owns client-only state** (`apps/web/src/store/`). Don't put server data in Zustand.

## Conventions

- Python: `from __future__ import annotations` at top of every module; `ruff` line length 100; `mypy --strict`. Tests use `pytest` asyncio auto-mode.
- The dev stack dogfoods the self-host distribution — Phase 0 production *is* the same docker-compose stack (doc 06 §10), so fix operational quirks in `infra/docker-compose.yml` + docs.
- License is **AGPL-3.0-only**; keep the SPDX intent in package manifests.
