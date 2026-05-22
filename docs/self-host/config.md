# Configuration reference

This page documents every environment variable that CivicSignals reads. It is
the authoritative reference for self-hosted deployments. See the
[quickstart](quickstart.md) for the minimal set you must change before starting
the stack.

## How configuration is loaded

All API and worker processes (api, worker\_ingest, worker\_extract,
worker\_score, worker\_notify, scheduler) share one Docker image. They load
configuration exclusively from environment variables. In the Docker Compose
stack every process receives the same `--env-file infra/.env` (or `../.env`
in dev), so a single file configures the whole backend.

The Python Settings class (`apps/api/src/civicsignals_api/config.py`) uses
Pydantic v2 `BaseSettings`. It reads variable names in **all-caps** from the
environment (Pydantic lowercases field names and looks them up case-insensitively).
The file also falls back to an `.env` file in the process working directory.

### Scope legend used below

| Scope | Meaning |
|---|---|
| `api` | FastAPI HTTP process |
| `worker_*` | All four Celery worker processes |
| `scheduler` | Celery beat process |
| `init` | One-shot `docker compose run --rm init` (seed + migrate) |
| `web` | Next.js frontend process |
| `compose` | Docker Compose and infrastructure services only — not read by Python |

---

## 1. Core / runtime

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `ENVIRONMENT` | Runtime environment. Controls logging verbosity and safety checks. | `development` | No | `api`, `worker_*`, `scheduler` |
| `LOG_LEVEL` | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). | `INFO` | No | `api`, `worker_*`, `scheduler` |
| `API_V1_PREFIX` | URL prefix for the versioned REST API. | `/api/v1` | No | `api` |
| `RECIPE_SCHEMA_DIR` | Absolute path to the JSON Schema directory for recipe validation. Defaults to the `packages/recipe-schema/` location discovered relative to the installed package. Override in containers where the repo root differs. | `None` (auto-discovered) | No | `api`, `worker_ingest` |
| `RECIPES_DIR` | Absolute path to the recipes directory. Defaults to the `recipes/` location discovered relative to the installed package. Override in containers where the repo root differs. | `None` (auto-discovered) | No | `api`, `worker_ingest` |

---

## 2. Database — Postgres and PgBouncer

> **Critical distinction — two database URLs serve different purposes:**
>
> All app traffic (FastAPI requests, Celery tasks) routes through **PgBouncer in
> transaction mode** on port **6432** (`DATABASE_URL`). PgBouncer caps the
> number of backend Postgres connections regardless of how many app processes
> are running, which lets many workers share a small connection pool safely.
>
> PgBouncer transaction mode has one trade-off: **no session-level state survives
> across query boundaries** in the same connection. This means `LISTEN/NOTIFY`,
> prepared transactions, and advisory locks cannot use `DATABASE_URL`. For those
> use cases — the Celery beat scheduler, long-running export jobs, and Alembic
> migrations — use `DATABASE_DIRECT_URL` (port **5432**), which connects directly
> to Postgres.
>
> Alembic's `env.py` always selects `DATABASE_DIRECT_URL` for this reason.
>
> See architecture doc §4 for the full rationale.

### 2a. Application database URLs

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `DATABASE_URL` | SQLAlchemy async URL routed through PgBouncer (port 6432). Used by all API and worker queries. | `postgresql+asyncpg://civic:civic@localhost:6432/civicsignals` | **Yes** | `api`, `worker_*`, `scheduler` |
| `DATABASE_DIRECT_URL` | SQLAlchemy async URL connecting directly to Postgres (port 5432). Used by Alembic migrations, `LISTEN/NOTIFY`, and long export jobs. | `None` (falls back to `DATABASE_URL` if unset — avoid in production) | **Yes** (production) | `init`, `scheduler` |

In the Docker Compose stack these look like:

```
# Routed through PgBouncer (transaction mode, high connection count)
DATABASE_URL=postgresql+asyncpg://civic:<POSTGRES_PASSWORD>@pgbouncer:6432/civicsignals

# Direct to Postgres (bypasses PgBouncer; required for Alembic and LISTEN/NOTIFY)
DATABASE_DIRECT_URL=postgresql+asyncpg://civic:<POSTGRES_PASSWORD>@postgres:5432/civicsignals
```

### 2b. Postgres container configuration (compose-level only)

These are read by the `postgres` service in Docker Compose, not by Python.
They must match the credentials embedded in `DATABASE_URL` and
`DATABASE_DIRECT_URL`.

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `POSTGRES_USER` | Postgres superuser name created on first boot. | `civic` | **Yes** | `compose` |
| `POSTGRES_PASSWORD` | Postgres superuser password. **Must be changed for production.** | `civic` | **Yes** (change for production) | `compose` |
| `POSTGRES_DB` | Name of the application database. | `civicsignals` | No | `compose` |

### 2c. PgBouncer tunables

PgBouncer is configured via environment variables by the `edoburu/pgbouncer`
image. The compose file sets sensible defaults; override them by adding these to
`infra/.env`.

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `MAX_CLIENT_CONN` | Maximum client connections PgBouncer accepts. | `1000` | No | `compose` |
| `DEFAULT_POOL_SIZE` | Postgres server connections per database/user pair. | `25` | No | `compose` |

---

## 3. Redis and Celery

Celery uses Redis as both its task broker and result backend. The cache layer
(signal feed pages, ICP score caches) also uses Redis.

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `REDIS_URL` | Redis URL for the application cache. Database index 0. | `redis://localhost:6379/0` | **Yes** | `api` |
| `CELERY_BROKER_URL` | Redis URL Celery uses to enqueue and dequeue tasks. Database index 1. | `redis://localhost:6379/1` | **Yes** | `api`, `worker_*`, `scheduler` |
| `CELERY_RESULT_BACKEND` | Redis URL where Celery stores task results. Database index 2. | `redis://localhost:6379/2` | **Yes** | `api`, `worker_*`, `scheduler` |

Use separate Redis database indices (0, 1, 2) as shown above to keep cache and
task data isolated. All three can point at the same Redis instance.

---

## 4. Object storage — S3 / MinIO

Raw scraped documents, FOIA response PDFs, and exported CSVs are stored in
S3-compatible object storage. The dev and default self-host stacks use MinIO;
you can point these at any S3-compatible provider (AWS S3, Cloudflare R2,
Backblaze B2, etc.).

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `S3_ENDPOINT_URL` | Override the S3 endpoint. Set to `http://minio:9000` for MinIO. Leave empty for native AWS S3. | `None` (AWS S3) | Only for non-AWS | `api`, `worker_*` |
| `S3_ACCESS_KEY_ID` | S3 / MinIO access key. For MinIO this is the `MINIO_ROOT_USER`. **Change for production.** | `civic` | **Yes** | `api`, `worker_*` |
| `S3_SECRET_ACCESS_KEY` | S3 / MinIO secret key. For MinIO this is the `MINIO_ROOT_PASSWORD`. **Change for production.** | `civic-secret` | **Yes** | `api`, `worker_*` |
| `S3_RAW_BUCKET` | Name of the bucket that stores raw scraped documents. Created automatically by `minio-init` on first boot. | `civic-raw` | No | `api`, `worker_*` |
| `S3_REGION` | AWS region (or equivalent for other providers). Affects request signing. | `us-east-1` | No | `api`, `worker_*` |

---

## 5. Email / SMTP

Transactional email (password reset, invitations, digest notifications) is sent
via SMTP. In development the stack runs Mailpit (`localhost:1025`) to capture
all outbound mail. In production point these at Resend, Postmark, SendGrid, or
any SMTP server.

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `SMTP_HOST` | SMTP server hostname. | `localhost` | **Yes** (production) | `api`, `worker_notify` |
| `SMTP_PORT` | SMTP server port. Use 587 for STARTTLS, 465 for SMTPS, 1025 for Mailpit. | `1025` | No | `api`, `worker_notify` |
| `EMAIL_FROM` | Sender address shown in all outbound mail. | `notifications@civicsignals.io` | No | `api`, `worker_notify` |

> **Note — `SMTP_USER`, `SMTP_PASSWORD`, and `SMTP_USE_TLS`** are present in
> `infra/.env.example` but are not yet declared as fields in
> `apps/api/src/civicsignals_api/config.py` (`extra = "ignore"` suppresses errors).
> They are consumed by `Settings` once the notification module (task N-series) is
> wired. Until then, set them in `.env` so they are ready — they will have no effect
> on the running app until the mailer is implemented.

---

## 6. Auth and secret keys

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `SECRET_KEY` | Master secret used for JWT signing and session-cookie signing. **Must be a long random value in production — never use the default.** Generate with `python -c "import secrets; print(secrets.token_hex(64))"`. | `dev-only-change-me` | **Yes** (change for production) | `api` |
| `ACCESS_TOKEN_TTL_SECONDS` | Lifetime of short-lived access tokens (JWTs), in seconds. | `3600` (1 hour) | No | `api` |

> **Note — `REFRESH_TOKEN_TTL_SECONDS`, `GOOGLE_OAUTH_CLIENT_ID`, and
> `GOOGLE_OAUTH_CLIENT_SECRET`** are present in `infra/.env.example` but are not
> yet declared in `config.py`. They will be wired in when the auth module (task B1
> / B2) is implemented. Set them in `.env` now so they are available when auth
> lands.

---

## 7. LLM providers

CivicSignals routes all LLM calls through an internal gateway
(`llm_gateway.py`). No module calls a vendor SDK directly. The gateway handles
model selection per task, token accounting per workspace, retry with backoff,
and prompt versioning.

You only need to set the key(s) for the provider(s) you actually use.
Vendor SDK packages are imported lazily, so unused provider keys are harmless
to leave empty.

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `LLM_DEFAULT_PROVIDER` | Provider to use when a task has no per-task override. One of `anthropic`, `openai`, `ollama`. | `anthropic` | No | `api`, `worker_extract`, `worker_score` |
| `ANTHROPIC_API_KEY` | Anthropic API key. Required when `LLM_DEFAULT_PROVIDER=anthropic` or when any task is routed to Anthropic. | `None` | Depends on provider | `api`, `worker_extract`, `worker_score` |
| `ANTHROPIC_BASE_URL` | Override the Anthropic API base URL (e.g., for a local proxy or enterprise endpoint). | `None` (Anthropic default) | No | `api`, `worker_extract`, `worker_score` |
| `OPENAI_API_KEY` | OpenAI API key. Required when using OpenAI for extraction or embeddings. | `None` | Depends on provider | `api`, `worker_extract`, `worker_score` |
| `OPENAI_BASE_URL` | Override the OpenAI API base URL (e.g., for Azure OpenAI or a local proxy). | `None` (OpenAI default) | No | `api`, `worker_extract`, `worker_score` |
| `OLLAMA_BASE_URL` | Base URL of a local Ollama server. Used when `LLM_DEFAULT_PROVIDER=ollama` or when tasks are routed to Ollama. | `http://localhost:11434` | Only if using Ollama | `api`, `worker_extract`, `worker_score` |
| `LLM_MAX_ATTEMPTS` | Total number of attempts (including the first) on transient LLM errors before the task fails. | `3` | No | `api`, `worker_extract`, `worker_score` |
| `LLM_TASK_MODELS` | JSON object mapping task names to `"provider:model"` strings (or just `"model"` to use the default provider). Overrides the gateway's built-in Haiku/Sonnet defaults for named tasks. Example: `{"classify":"anthropic:claude-3-5-haiku-latest"}`. | `{}` | No | `api`, `worker_extract`, `worker_score` |

### Choosing a provider

| Scenario | Recommended setting |
|---|---|
| Cloud / managed | `LLM_DEFAULT_PROVIDER=anthropic` + `ANTHROPIC_API_KEY=<key>` |
| Self-host, no external API | `LLM_DEFAULT_PROVIDER=ollama` + `OLLAMA_BASE_URL=http://ollama:11434` |
| Self-host BYO key | `LLM_DEFAULT_PROVIDER=anthropic` or `openai` + matching key |
| Mixed (cheap extraction, smart search on frontier) | Use `LLM_TASK_MODELS` to route tasks selectively |

---

## 8. Admin seed (first-boot only)

These variables are used by the `init` process type
(`docker compose run --rm init`) to create the first admin user and workspace.
They are read by `apps/api/src/civicsignals_api/scripts/seed_admin.py`, not by
the Pydantic `Settings` class.

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `CIVICSIGNALS_ADMIN_EMAIL` | Email address for the initial admin account. | None | **Yes** (first boot) | `init` |
| `CIVICSIGNALS_ADMIN_PASSWORD` | Password for the initial admin account. **Change immediately after first login.** | None | **Yes** (first boot) | `init` |

> These variables are only consumed during the `init` run. The running API does
> not read them. They can be removed from `.env` after first boot if you prefer,
> but leaving them in is harmless.
>
> Full admin user creation requires task B1 (auth models) and B5 (workspace
> models) to be complete. Until then, `init` verifies the DB connection and
> enables the pgvector extension, then prints a stub reminder.

---

## 9. Deployment (compose-level)

These variables are consumed by Docker Compose or the nginx reverse proxy only.
They are not read by Python.

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `CIVIC_VERSION` | Image tag for `ghcr.io/civicsignals/api` and `ghcr.io/civicsignals/web`. Use a specific release tag (e.g., `v0.1.0`) in production. | `latest` | No | `compose` |
| `CIVIC_BASE_URL` | Public URL of the deployment (e.g., `https://app.example.com`). Used for link generation, CORS, and cookie domain. | None | **Yes** (production) | `compose` |
| `HTTP_PORT` | Host port nginx listens on for HTTP traffic. | `80` | No | `compose` |
| `HTTPS_PORT` | Host port nginx listens on for HTTPS traffic. | `443` | No | `compose` |

---

## 10. Frontend (Next.js)

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Public URL the browser uses to call the API (e.g., `https://app.example.com/api/v1`). Must be reachable from the browser — set to the nginx public address, not a container-internal address. | `http://localhost:8000/api/v1` | **Yes** (production) | `web` |

---

## 11. Observability

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `SENTRY_DSN` | Sentry (or GlitchTip) DSN for error tracking. Leave empty to disable. Self-hosters can point this at a self-hosted Sentry or GlitchTip instance. | `""` | No | `api`, `worker_*`, `scheduler` |

> OpenTelemetry (traces) and Prometheus (metrics) are wired in the architecture
> plan (doc 06 §9) but the environment variables for OTel exporters and Prometheus
> are not yet declared in `config.py`. They will be added in task LC-15 (observability
> wiring). Expect variables such as `OTEL_EXPORTER_OTLP_ENDPOINT` and
> `PROMETHEUS_MULTIPROC_DIR` in a future release.

---

## 12. Feature flags and telemetry

| Variable | Description | Default | Required | Scope |
|---|---|---|---|---|
| `CIVICSIGNALS_TELEMETRY` | Set to `true` to send anonymous usage pings (counts of signals viewed, CRM pushes, etc.) that fund recipe quality improvements. **Never sends signal content or PII.** Off by default on self-host. | `false` | No | `api` |

> `CIVICSIGNALS_TELEMETRY` is declared in `infra/.env.example` but not yet in
> `config.py`. It will be wired in task O6 (telemetry opt-in).

---

## Secrets that must change for production

The following variables have insecure development defaults. **Change all of them
before exposing the stack to the internet:**

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

See the [quickstart](quickstart.md) for the full first-boot checklist.

---

## Discrepancies between config.py and infra/.env.example

The following variables appear in `infra/.env.example` but are **not yet
declared as fields in `apps/api/src/civicsignals_api/config.py`**. Pydantic
`Settings` is configured with `extra = "ignore"` so the app starts without
error, but these vars have no effect until the corresponding tasks are complete.
This is intentional: the `.env.example` is forward-looking so operators can set
everything at install time without reconfiguring later.

| Variable | Planned wiring task |
|---|---|
| `SMTP_USER` | N-series (notifications module) |
| `SMTP_PASSWORD` | N-series (notifications module) |
| `SMTP_USE_TLS` | N-series (notifications module) |
| `REFRESH_TOKEN_TTL_SECONDS` | B1 (auth module) |
| `GOOGLE_OAUTH_CLIENT_ID` | B2 (Google OAuth) |
| `GOOGLE_OAUTH_CLIENT_SECRET` | B2 (Google OAuth) |
| `CIVICSIGNALS_TELEMETRY` | O6 (telemetry opt-in) |
| `SENTRY_DSN` | LC-15 (observability wiring) |
| `CIVIC_BASE_URL` | Compose/nginx level only (no Python field needed) |
| `HTTP_PORT` | Compose/nginx level only |
| `HTTPS_PORT` | Compose/nginx level only |
| `CIVIC_VERSION` | Compose image tag only |
| `CIVICSIGNALS_ADMIN_EMAIL` | Read by `seed_admin.py`, not `config.py` (by design) |
| `CIVICSIGNALS_ADMIN_PASSWORD` | Read by `seed_admin.py`, not `config.py` (by design) |
| `NEXT_PUBLIC_API_BASE_URL` | Next.js build-time variable, not Python |
