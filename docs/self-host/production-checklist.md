# Production prerequisites checklist

The [quickstart](quickstart.md) gets the stack *running*. This page is the
checklist for a **working production deployment** — one where real users sign in
and receive *real signals* about *real* public-sector entities.

A stack that boots is not the same as a stack that produces signals. The
extraction pipeline calls an LLM, matches against an entity directory, and runs
ingestion recipes; if any of those three is unprovisioned, the feed will be
empty even though every container reports healthy. Work through this list before
inviting users.

> All settings below are documented in full in the
> [configuration reference](config.md). Set them in `infra/.env` (the file the
> compose stack reads via `--env-file`).

---

## 1. LLM provider key — **hard requirement**

**Without an LLM key, no signals are ever produced.** The extraction pipeline
calls the LLM at the relevance gate, entity-extraction, and signal-type
classification stages. The real provider backends **hard-fail** when their key
is missing — the Anthropic backend raises `BackendNotAvailableError`
("AnthropicBackend requires ANTHROPIC_API_KEY") on the first call, which fails
the extraction task. Documents get ingested and stored but never become signals.

Configure **one** provider:

| Provider | Settings | Notes |
|---|---|---|
| **Anthropic** (default) | `ANTHROPIC_API_KEY` | `LLM_DEFAULT_PROVIDER=anthropic` (the default). |
| **OpenAI** | `OPENAI_API_KEY`, `LLM_DEFAULT_PROVIDER=openai` | |
| **Ollama** (self-hosted, no API key) | `OLLAMA_BASE_URL=http://your-ollama:11434`, `LLM_DEFAULT_PROVIDER=ollama` | Run your own model server; no per-token cost. |

Leave `LLM_BACKEND` at its default (`vendor`). **`LLM_BACKEND=fake` is for
tests/e2e only** — it produces deterministic, scripted output and must never be
used in production (it would manufacture fake signals).

Verify the key works before relying on it:

```bash
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  exec api python -c "import os; assert os.environ.get('ANTHROPIC_API_KEY'), 'no key'"
```

---

## 2. Entity data — load the real directories

The default `init` seed creates the admin user and workspace but **does not load
the entity directory**. The repo ships only tiny *sample* fixtures (a handful of
rows per source) so CI stays fast. With only the samples loaded, ICP geo/kind
matching has almost nothing to match against and the public directory is
near-empty.

Load the **full** public-domain directories — NCES CCD (K-12 districts), IPEDS
HD (higher-ed), and the Census of Governments (state/local governments):

```bash
# Offline / smoke: load the bundled sample fixtures (a few rows each).
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  run --rm api load-entities --source all

# Production: download each real dataset first, then load per source.
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  run --rm -v /data:/data api load-entities --source nces   --path-or-url /data/ccd_lea.csv
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  run --rm -v /data:/data api load-entities --source ipeds  --path-or-url /data/ipeds_hd.csv
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  run --rm -v /data:/data api load-entities --source census --path-or-url /data/cog_units.csv
```

The loader is **idempotent** (it UPSERTs on the NCES LEAID / IPEDS UnitID /
Census GID natural key), so re-run it whenever a directory publishes a new
vintage. `--path-or-url` accepts a local path **or** an `http(s)://` URL.

Dataset download pages:

- **NCES CCD** (K-12 LEA universe): <https://nces.ed.gov/ccd/ccddata.asp>
- **IPEDS HD** (institutional characteristics directory): <https://nces.ed.gov/ipeds/use-the-data>
- **Census of Governments** (Government Units file, ~90k rows): <https://www.census.gov/programs-surveys/cog.html>

> The bundled `STATE_FIPS` table (used to synthesize the state-government parent
> rows) covers only the states the sample fixtures touch. To parent every entity
> nationwide, extend `STATE_FIPS` in `modules/entities/seeding.py` to all 50
> states (or load it from the FIPS reference download, doc 16 §12c) before
> loading the full datasets.

---

## 3. Required secrets / env with no safe default

These have placeholder or empty defaults that are **unsafe in production** — set
real values for every one:

| Variable | Why it matters |
|---|---|
| `SECRET_KEY` | Signs JWTs and (by fallback) encrypts MFA TOTP secrets, OAuth state, and integration tokens. The default `dev-only-change-me` is publicly known — anyone could forge a session. Generate a long random value: `openssl rand -hex 32`. |
| `POSTGRES_PASSWORD` / DB URLs | `DATABASE_URL` (app, via PgBouncer :6432) and `DATABASE_DIRECT_URL` (Alembic / `init` / `load-entities`, direct :5432). |
| `CIVICSIGNALS_ADMIN_EMAIL`, `CIVICSIGNALS_ADMIN_PASSWORD` | The first admin account `init` creates. If unset, `init` skips admin creation and you can't log in. |
| `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Celery broker/result store — the workers do nothing without a reachable Redis. |
| `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_RAW_BUCKET` | Raw-document and export object storage (MinIO in the bundled stack). |

Consider also setting dedicated keys (rather than letting them fall back to
`SECRET_KEY`) so a future `SECRET_KEY` rotation doesn't invalidate stored
secrets: `MFA_TOTP_ENCRYPTION_KEY`, `INTEGRATIONS_TOKEN_ENCRYPTION_KEY`.

In any non-localhost deployment also set the public URLs so emails and CORS
work: `WEB_BASE_URL`, `API_BASE_URL`, and `CORS_ALLOW_ORIGINS` (the browser
blocks authed requests, including login, without a matching CORS origin).

---

## 4. Email (SMTP) — only if you require verification

`REQUIRE_EMAIL_VERIFICATION` defaults to **false**, so the first-run flow works
without any mail server. If you enable it (`REQUIRE_EMAIL_VERIFICATION=true`),
**users cannot log in until they verify their email**, so you must configure a
real SMTP relay or verification mail will silently fail:

```ini
REQUIRE_EMAIL_VERIFICATION=true
SMTP_HOST=smtp.resend.com        # or Postmark, SES, etc.
SMTP_PORT=587
EMAIL_FROM=notifications@your-domain.com
# plus the credentials your relay requires
```

Email digests (saved-search notifications) also need a working SMTP relay to be
delivered. The bundled dev stack points SMTP at Mailpit (a catch-all inbox), not
a real relay — do not leave it pointed there in production.

---

## 5. Recipes — ingestion coverage grows over time

Signals come from **recipes**: declarative YAML that instantiates a connector
for one source (a city portal, a board-docs site, a grants portal, …). Only a
small set of real recipes ship today (~37 under `recipes/`), so out of the box
the feed covers only those sources.

Coverage grows by adding recipes — contribute or request them via recipe PRs to
the repo. Until a recipe exists for a source you care about, no signals from that
source will appear. (For a quick proof-of-life you can also create a signal via a
manual document upload — see the smoke test below.)

---

## 6. Embeddings — optional, but recommended

Embeddings power semantic search and fuzzy de-duplication. They are **optional**:
the core scored feed works without them (it falls back to keyword/FTS retrieval
and exact-key dedupe). Without an embeddings provider, semantic search and
fuzzy-dedupe degrade gracefully — they don't break.

To enable, configure an embeddings provider (Anthropic has no embeddings
endpoint, so this is separate from your completion provider):

```ini
EMBEDDING_PROVIDER=openai          # or ollama
OPENAI_API_KEY=...                 # if provider=openai
# EMBEDDING_DIM must match the signals_signal.vector_embedding column width (1536).
```

---

## Smoke test after deploy

Once the stack is up and the prerequisites above are set, confirm the full path
produces a signal in a workspace feed:

1. **Migrate + seed admin** (creates the admin user + workspace):
   ```bash
   docker compose -f infra/docker-compose.yml --env-file infra/.env run --rm init
   ```
2. **Load entities** (at minimum the samples, ideally the real datasets):
   ```bash
   docker compose -f infra/docker-compose.yml --env-file infra/.env \
     run --rm api load-entities --source all
   ```
   Confirm it prints a non-zero `entities=` summary.
3. **Produce a signal** — either enable a recipe for a source whose entities you
   loaded (and let the scheduler/ingest workers run), or upload a document
   manually so the extraction pipeline runs against it.
4. **Confirm it lands in a feed** — sign in as the admin, set an ICP on the
   workspace that matches the entity's state/kind, and verify the signal appears
   in the workspace signal feed. An empty feed after this almost always means the
   LLM key (step 1) or entity data (step 2) is missing.

---

## Related docs

- [Quickstart](quickstart.md) — minimal install
- [Configuration reference](config.md) — every environment variable
- [Upgrade guide](upgrade.md) — version upgrades
