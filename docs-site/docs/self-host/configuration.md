---
id: configuration
title: Configuration Reference
sidebar_label: Configuration
slug: /self-host/configuration
---

# Configuration reference

This page documents every environment variable that CivicSignals reads. It is the authoritative reference for self-hosted deployments. See the [quickstart](./quickstart.md) for the minimal set you must change before starting the stack.

## How configuration is loaded

All API and worker processes (`api`, `worker_ingest`, `worker_extract`, `worker_score`, `worker_notify`, `scheduler`) share one Docker image. They load configuration exclusively from environment variables. In the Docker Compose stack every process receives the same `--env-file infra/.env`, so a single file configures the whole backend.

The Python Settings class (`apps/api/src/civicsignals_api/config.py`) uses Pydantic v2 `BaseSettings`. Variables are read in **all-caps** from the environment.

### Scope legend

| Scope | Meaning |
|---|---|
| `api` | FastAPI HTTP process |
| `worker_*` | All four Celery worker processes |
| `scheduler` | Celery beat process |
| `init` | One-shot `docker compose run --rm init` (migrations + admin seed) |
| `web` | Next.js frontend process |
| `compose` | Docker Compose and infrastructure services only — not read by Python |

---

## 1. Core / runtime

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `ENVIRONMENT` | Runtime environment. Controls logging verbosity and safety checks. | `development` | No | `api`, `worker_*`, `scheduler` |
| `LOG_LEVEL` | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). | `INFO` | No | `api`, `worker_*`, `scheduler` |
| `API_V1_PREFIX` | URL prefix for the versioned REST API. | `/api/v1` | No | `api` |
| `RECIPE_SCHEMA_DIR` | Absolute path to the JSON Schema directory for recipe validation. Defaults to the `packages/recipe-schema/` location auto-discovered relative to the installed package. | `None` (auto-discovered) | No | `api`, `worker_ingest` |
| `RECIPES_DIR` | Absolute path to the recipes directory. Defaults to the `recipes/` location auto-discovered relative to the installed package. | `None` (auto-discovered) | No | `api`, `worker_ingest` |

---

## 2. Database — Postgres and PgBouncer

:::info Two database URLs
All app traffic routes through **PgBouncer in transaction mode** on port **6432** (`DATABASE_URL`). PgBouncer caps backend Postgres connections regardless of how many app processes are running.

PgBouncer transaction mode has one trade-off: **no session-level state survives across query boundaries**. This means `LISTEN/NOTIFY`, prepared transactions, and advisory locks cannot use `DATABASE_URL`. For those use cases — Alembic migrations, long-running export jobs — use `DATABASE_DIRECT_URL` (port **5432**), which connects directly to Postgres.

See [Production — PgBouncer](./production.md#pgbouncer-transaction-mode-vs-direct-connection) for more detail.
:::

### 2a. Application database URLs

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `DATABASE_URL` | SQLAlchemy async URL via PgBouncer (port 6432). Used by all API and worker queries. | `postgresql+asyncpg://civic:civic@localhost:6432/civicsignals` | **Yes** | `api`, `worker_*`, `scheduler` |
| `DATABASE_DIRECT_URL` | SQLAlchemy async URL direct to Postgres (port 5432). Used by Alembic, `LISTEN/NOTIFY`, and long export jobs. Falls back to `DATABASE_URL` if unset — **avoid in production**. | `None` | **Yes** (production) | `init`, `scheduler` |

Example values in `infra/.env`:

```dotenv
DATABASE_URL=postgresql+asyncpg://civic:<POSTGRES_PASSWORD>@pgbouncer:6432/civicsignals
DATABASE_DIRECT_URL=postgresql+asyncpg://civic:<POSTGRES_PASSWORD>@postgres:5432/civicsignals
```

### 2b. Postgres container configuration (compose-level only)

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `POSTGRES_USER` | Postgres superuser name created on first boot. | `civic` | **Yes** | `compose` |
| `POSTGRES_PASSWORD` | Postgres superuser password. **Must be changed for production.** | `civic` | **Yes** (change for production) | `compose` |
| `POSTGRES_DB` | Name of the application database. | `civicsignals` | No | `compose` |

### 2c. PgBouncer tunables

| Variable | Description | Default | Scope |
|---|---|---|---|
| `MAX_CLIENT_CONN` | Maximum client connections PgBouncer accepts. | `1000` | `compose` |
| `DEFAULT_POOL_SIZE` | Postgres server connections per database/user pair. | `25` | `compose` |

---

## 3. Redis and Celery

Celery uses Redis as both its task broker and result backend. The application cache layer also uses Redis.

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `REDIS_URL` | Redis URL for the application cache (database index 0). | `redis://localhost:6379/0` | **Yes** | `api` |
| `CELERY_BROKER_URL` | Redis URL Celery uses to enqueue and dequeue tasks (database index 1). | `redis://localhost:6379/1` | **Yes** | `api`, `worker_*`, `scheduler` |
| `CELERY_RESULT_BACKEND` | Redis URL where Celery stores task results (database index 2). | `redis://localhost:6379/2` | **Yes** | `api`, `worker_*`, `scheduler` |

Use separate Redis database indices (0, 1, 2) as shown above to keep cache and task data isolated. All three can point at the same Redis instance.

---

## 4. Object storage — S3 / MinIO

Raw scraped documents, FOIA response PDFs, and exported CSVs are stored in S3-compatible object storage. The default self-host stack uses MinIO; you can point these at any S3-compatible provider (AWS S3, Cloudflare R2, Backblaze B2, etc.).

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `S3_ENDPOINT_URL` | Override the S3 endpoint. Set to `http://minio:9000` for MinIO. Leave empty for native AWS S3. | `None` (AWS S3) | Only for non-AWS | `api`, `worker_*` |
| `S3_ACCESS_KEY_ID` | S3 / MinIO access key. For MinIO this is the `MINIO_ROOT_USER`. **Change for production.** | `civic` | **Yes** | `api`, `worker_*` |
| `S3_SECRET_ACCESS_KEY` | S3 / MinIO secret key. For MinIO this is the `MINIO_ROOT_PASSWORD`. **Change for production.** | `civic-secret` | **Yes** | `api`, `worker_*` |
| `S3_RAW_BUCKET` | Bucket for raw scraped documents. Created automatically by `minio-init` on first boot. | `civic-raw` | No | `api`, `worker_*` |
| `S3_REGION` | AWS region (or equivalent). Affects request signing. | `us-east-1` | No | `api`, `worker_*` |

---

## 5. Email / SMTP

Transactional email (password reset, invitations, digest notifications) is sent via SMTP. In development the stack runs Mailpit (`localhost:1025`) to capture all outbound mail. In production point these at Resend, Postmark, SendGrid, or any SMTP server.

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `SMTP_HOST` | SMTP server hostname. | `localhost` | **Yes** (production) | `api`, `worker_notify` |
| `SMTP_PORT` | SMTP server port (587 for STARTTLS, 465 for SMTPS, 1025 for Mailpit). | `1025` | No | `api`, `worker_notify` |
| `EMAIL_FROM` | Sender address for all outbound mail. | `notifications@civicsignals.io` | No | `api`, `worker_notify` |

:::note Pending variables
`SMTP_USER`, `SMTP_PASSWORD`, and `SMTP_USE_TLS` are present in `infra/.env.example` but are not yet wired in `config.py` (pending notifications module). Set them in `.env` now so they are ready — they have no effect until the mailer is implemented.
:::

---

## 6. Auth and secret keys

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `SECRET_KEY` | Master secret for JWT signing and session-cookie signing. **Never use the default in production.** Generate with `python3 -c "import secrets; print(secrets.token_hex(64))"`. | `dev-only-change-me` | **Yes** (change) | `api` |
| `JWT_SECRET` | Dedicated secret for JWT signing. Falls back to `SECRET_KEY` when unset. | `None` | No | `api` |
| `JWT_ALGORITHM` | JWT signing algorithm. | `HS256` | No | `api` |
| `JWT_ISSUER` | `iss` claim set on issued tokens and verified on decode. | `civicsignals` | No | `api` |
| `ACCESS_TOKEN_TTL_SECONDS` | Lifetime of access tokens, in seconds. | `3600` (1 h) | No | `api` |
| `REFRESH_TOKEN_TTL_SECONDS` | Lifetime of refresh tokens, in seconds. | `2592000` (30 d) | No | `api` |
| `EMAIL_VERIFICATION_TTL_SECONDS` | Lifetime of email-verification tokens, in seconds. | `86400` (24 h) | No | `api` |
| `REQUIRE_EMAIL_VERIFICATION` | When `true`, users must verify their email before logging in. | `false` | No | `api` |
| `WEB_BASE_URL` | Base URL of the web app, used to build verification-email links. | `http://localhost:3000` | **Yes** (production) | `api` |

---

## 7. LLM providers

CivicSignals routes all LLM calls through an internal gateway (`llm_gateway.py`). No module calls a vendor SDK directly. Set only the key(s) for providers you actually use.

| Variable | Description | Default | Scope |
|---|---|---|---|
| `LLM_DEFAULT_PROVIDER` | Provider for tasks with no per-task override. One of `anthropic`, `openai`, `ollama`. | `anthropic` | `api`, `worker_extract`, `worker_score` |
| `ANTHROPIC_API_KEY` | Anthropic API key. Required when `LLM_DEFAULT_PROVIDER=anthropic`. | `None` | `api`, `worker_extract`, `worker_score` |
| `ANTHROPIC_BASE_URL` | Override the Anthropic API base URL (e.g. for a local proxy). | `None` | `api`, `worker_extract`, `worker_score` |
| `OPENAI_API_KEY` | OpenAI API key. Required when using OpenAI. | `None` | `api`, `worker_extract`, `worker_score` |
| `OPENAI_BASE_URL` | Override the OpenAI API base URL (e.g. for Azure OpenAI). | `None` | `api`, `worker_extract`, `worker_score` |
| `OLLAMA_BASE_URL` | Base URL of a local Ollama server. | `http://localhost:11434` | `api`, `worker_extract`, `worker_score` |
| `LLM_MAX_ATTEMPTS` | Total attempts on transient LLM errors before the task fails. | `3` | `api`, `worker_extract`, `worker_score` |
| `LLM_TASK_MODELS` | JSON object mapping task names to `"provider:model"` strings. Example: `{"classify":"anthropic:claude-3-5-haiku-latest"}`. | `{}` | `api`, `worker_extract`, `worker_score` |

### Choosing a provider

| Scenario | Recommended setting |
|---|---|
| Cloud / managed | `LLM_DEFAULT_PROVIDER=anthropic` + `ANTHROPIC_API_KEY=<key>` |
| Self-host, no external API | `LLM_DEFAULT_PROVIDER=ollama` + `OLLAMA_BASE_URL=http://ollama:11434` |
| Self-host BYO key | `LLM_DEFAULT_PROVIDER=anthropic` or `openai` + matching key |
| Mixed (cheap extraction, frontier for search) | Use `LLM_TASK_MODELS` to route tasks selectively |

---

## 8. Admin seed (first-boot only)

These variables are consumed by `docker compose run --rm init` to create the first admin user and workspace. They are read by `apps/api/src/civicsignals_api/scripts/seed_admin.py`, not by the Pydantic `Settings` class.

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `CIVICSIGNALS_ADMIN_EMAIL` | Email for the initial admin account. | None | **Yes** (first boot) | `init` |
| `CIVICSIGNALS_ADMIN_PASSWORD` | Password for the initial admin account. **Change immediately after first login.** | None | **Yes** (first boot) | `init` |

These variables are only read during the `init` run. The running API does not read them.

---

## 9. Deployment (compose-level)

| Variable | Description | Default | Scope |
|---|---|---|---|
| `CIVIC_VERSION` | Image tag for `ghcr.io/civicsignals/api` and `ghcr.io/civicsignals/web`. Use a specific release tag (e.g. `v0.1.0`) in production. | `latest` | `compose` |
| `CIVIC_BASE_URL` | Public URL of the deployment (e.g. `https://app.example.com`). Used for link generation, CORS, and cookie domain. | None | `compose` |
| `HTTP_PORT` | Host port nginx listens on for HTTP. | `80` | `compose` |
| `HTTPS_PORT` | Host port nginx listens on for HTTPS. | `443` | `compose` |

---

## 10. Frontend (Next.js)

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Public URL the browser uses to call the API (e.g. `https://app.example.com/api/v1`). Must be reachable from the browser — set to the nginx public address, not a container-internal address. | `http://localhost:8000/api/v1` | **Yes** (production) | `web` |

---

## 11. Observability

| Variable | Description | Default | Scope |
|---|---|---|---|
| `SENTRY_DSN` | Sentry (or GlitchTip) DSN for error tracking. Leave empty to disable. | `""` | `api`, `worker_*`, `scheduler` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP/gRPC endpoint, e.g. `http://otel-collector:4317`. Unset = OTel disabled. | _(unset)_ | `api`, `worker_*` |
| `OTEL_SERVICE_NAME` | OTel `service.name` attribute. | `civicsignals-api` | `api`, `worker_*` |
| `OTEL_TRACES_SAMPLE_RATIO` | Head-based sampling ratio for OTel traces (1.0 = all). | `1.0` | `api`, `worker_*` |
| `GRAFANA_PORT` | Host port for Grafana (default avoids conflict with Next.js :3000). | `3100` | `compose` |

See `docs/self-host/observability.md` for the full observability guide.

---

## 12. Feature flags

| Variable | Description | Default | Scope |
|---|---|---|---|
| `CIVICSIGNALS_TELEMETRY` | Set to `true` to send anonymous usage pings (never sends signal content or PII). Off by default on self-host. | `false` | `api` |

---

## Secrets that must change for production

:::danger Change before going live
The following variables have insecure development defaults. Change **all of them** before exposing the stack to the internet.
:::

| Variable | Dev default | Why it must change |
|---|---|---|
| `POSTGRES_PASSWORD` | `civic` | Anyone who can reach port 5432 can authenticate |
| `DATABASE_URL` | contains `civic:civic` | Must embed the new `POSTGRES_PASSWORD` |
| `DATABASE_DIRECT_URL` | contains `civic:civic` | Same |
| `S3_ACCESS_KEY_ID` | `civic` | Anyone who can reach MinIO can read/write all buckets |
| `S3_SECRET_ACCESS_KEY` | `civic-secret` | Same |
| `SECRET_KEY` | `dev-only-change-me` | Compromise allows forging JWTs and session cookies |
| `CIVICSIGNALS_ADMIN_PASSWORD` | (must be set) | First admin account |
| `SMTP_USER` / `SMTP_PASSWORD` | empty | Required for authenticated SMTP in production |

## Discrepancies between config.py and .env.example

The following variables appear in `infra/.env.example` but are **not yet declared as fields in `config.py`**. Pydantic `Settings` is configured with `extra = "ignore"` so the app starts without error, but these vars have no effect until the corresponding tasks are complete. This is intentional: `.env.example` is forward-looking so operators can set everything at install time.

| Variable | Planned wiring task |
|---|---|
| `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_USE_TLS` | N-series (notifications module) |
| `REFRESH_TOKEN_TTL_SECONDS` | B1 (auth module) |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | B2 (Google OAuth) |
| `CIVICSIGNALS_TELEMETRY` | O6 (telemetry opt-in) |
| `SENTRY_DSN` | LC-15 (observability wiring) |
| `CIVIC_BASE_URL`, `HTTP_PORT`, `HTTPS_PORT`, `CIVIC_VERSION` | Compose/nginx level only |
| `CIVICSIGNALS_ADMIN_EMAIL`, `CIVICSIGNALS_ADMIN_PASSWORD` | Read by `seed_admin.py`, not `config.py` (by design) |
| `NEXT_PUBLIC_API_BASE_URL` | Next.js build-time variable, not Python |
