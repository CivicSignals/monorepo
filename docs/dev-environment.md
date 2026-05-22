# Local development environment (TODO A2)

`make dev` is the single command that brings up the full local stack. It mirrors
the Phase 0 single-VPS production layout (doc 06 §10), so the dev stack *is* the
self-host distribution — we dogfood it from day one.

## Prerequisites

- **Docker** + the Compose v2 plugin (`docker compose`, not the legacy
  `docker-compose`).
- For running apps outside Docker:
  - **Node 22** (`.nvmrc`) + **pnpm 9** — `corepack enable`.
  - **Python 3.12** (`.python-version`) + **[uv](https://docs.astral.sh/uv/)**.

`uv` and `pnpm` are not needed just to run `make dev` (everything runs in
containers), but they are needed for the per-app workflows below.

## One command

```bash
make dev
```

That target:

1. `make env` — creates `.env` from `.env.example` if it is missing.
2. `docker compose -f infra/docker-compose.dev.yml up --build` — builds the API
   image and starts every service.

Services and ports:

| Service          | Port(s)        | Notes                                            |
|------------------|----------------|--------------------------------------------------|
| `postgres`       | 5432           | PostgreSQL 16 + pgvector                          |
| `pgbouncer`      | 6432           | transaction-mode pool; app traffic goes here     |
| `redis`          | 6379           | Celery broker + cache (AOF on)                    |
| `minio`          | 9000 / 9001    | S3-compatible blob store (`minio-init` makes the bucket) |
| `mailpit`        | 1025 / 8025    | SMTP capture (1025) + web UI (8025)               |
| `api`            | 8000           | FastAPI — `/docs`, `/api/v1/openapi.json`, `/healthz` |
| `worker_*`       | —              | the four Celery worker process types             |
| `scheduler`      | —              | Celery beat (singleton)                           |
| `web`            | 3000           | Next.js dev server                                |

## Startup ordering

Ordering is enforced with healthchecks + `depends_on`, so a fresh stack comes up
clean without manual sequencing:

```
postgres (healthy) -> pgbouncer (healthy)
pgbouncer / redis / minio (healthy) -> migrate (alembic upgrade head)
migrate (completed) -> api + 4 workers + scheduler
api (healthy) -> web
```

The `migrate` one-shot service runs `alembic upgrade head` against the **direct**
Postgres URL (bypassing PgBouncer, per doc 06 §4) and exits; the app processes
wait on it via `service_completed_successfully`, so the schema is always current
before the API serves traffic.

> Until the module models land (most are still stubs — only A1 is DONE), there
> are no migrations to apply, so `migrate` is a fast no-op. It is wired now so the
> ordering is correct the moment C1/E4/B5 add tables.

## Migrate and seed

`make dev` already migrates on startup. To run migrations manually (e.g. after
adding a revision):

```bash
make migrate                     # one-shot container: alembic upgrade head
```

Seed a demo workspace + synthetic signals so a fresh checkout shows data:

```bash
make seed                        # idempotent — safe to re-run
```

The seed (`apps/api/src/civicsignals_api/scripts/seed_demo.py`) creates:

- a **demo workspace** (`demo`) and **demo user** (`demo@civicsignals.io`),
- three synthetic public-sector **entities**,
- five synthetic **signals** across MVP signal types (RFP, contract-expiring,
  budget, leadership change, board agenda).

Because the real domain tables don't exist yet, the seed owns dedicated,
dev-only `dev_seed_*` tables that it creates itself (`CREATE TABLE IF NOT EXISTS`)
and that Alembic does **not** migrate. When the owning modules ship their models,
the seed should switch to calling their `services.py` (TODO B1/B5 accounts,
TODO C1 entities, TODO E4 signals) and the `dev_seed_*` tables can be dropped.

## Other targets

```bash
make config         # validate / render the resolved compose config
make dev-detached   # same as `make dev`, detached
make logs           # tail all stack logs
make down           # stop the stack
make clean          # stop + remove volumes (DESTROYS local data)
```

## Running apps without Docker

```bash
# API (needs Postgres + Redis reachable; point .env at localhost ports)
cd apps/api && uv sync && uv run uvicorn civicsignals_api.main:app --reload --port 8000

# Web
pnpm install && pnpm --filter @civicsignals/web dev
```

Python deps are managed per app with `uv` (each app has its own `uv`-managed
virtualenv); Node deps are managed with `pnpm` workspaces (Turborepo's default).
