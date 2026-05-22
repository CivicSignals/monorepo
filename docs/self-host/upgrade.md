# Self-host upgrade guide

<!-- Task O5 — upgrade path: automated migrations + version compatibility matrix -->

This guide covers upgrading a self-hosted CivicSignals instance from one release to the
next. The upgrade process is designed to be safe, idempotent, and non-destructive:

- Database migrations use [Alembic](https://alembic.sqlalchemy.org/) and are always
  forward-only and idempotent — running `alembic upgrade head` on an already-current
  schema is a no-op.
- Docker image tags are immutable. Pulling a new tag never overwrites the old one.
- Persistent data lives in named Docker volumes (`pgdata`, `redisdata`, `miniodata`,
  `certdata`) which are never touched by `docker compose pull` or `up -d`.

Related docs:
- [Quickstart](quickstart.md) — initial installation
- [Configuration reference](config.md) — all environment variables (task O4)
- [Backup and DR](backup.md) — `pg_dump` procedure and Backblaze B2 (task LC-8)

---

## Before you upgrade: back up first

**Always back up the database before upgrading**, even for patch releases. If something
goes wrong you need a clean restore point.

```bash
# Replace <POSTGRES_USER> and <POSTGRES_DB> with the values from your infra/.env.
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  exec postgres pg_dump -U <POSTGRES_USER> <POSTGRES_DB> \
  | gzip > backup-$(date +%Y%m%d%H%M%S).sql.gz
```

Store the backup somewhere outside the host — Backblaze B2, S3, or your preferred
off-site storage. See [backup.md](backup.md) for the automated nightly `pg_dump` setup
recommended for production.

---

## Standard upgrade procedure

The steps below work for every release type (patch, minor, major) unless the release
notes call out additional steps. Follow the [version compatibility matrix](#version-compatibility-matrix)
before skipping versions.

```bash
# 0. Navigate to your install directory
cd /opt/civicsignals   # or wherever your infra/.env lives

# 1. Back up the database (see above)

# 2. Pull the new images
docker compose -f infra/docker-compose.yml --env-file infra/.env pull

# 3. Run migrations against the new schema
#    This uses the `migrate` one-shot service which:
#      - connects on DATABASE_DIRECT_URL (port 5432, bypasses PgBouncer)
#      - runs `alembic upgrade head` (no-op if already current)
#      - exits 0 on success, non-zero on failure
#    App services will NOT restart until this completes successfully.
docker compose -f infra/docker-compose.yml --env-file infra/.env run --rm migrate

# 4. Restart the stack with the new images
docker compose -f infra/docker-compose.yml --env-file infra/.env up -d
```

When you run `up -d`, Docker Compose restarts any service whose image digest has
changed. The `migrate` service also runs automatically as a dependency of `api`,
`worker_ingest`, `worker_extract`, `worker_score`, `worker_notify`, and `scheduler`
via `depends_on: migrate: condition: service_completed_successfully`. This means the
app will not serve traffic until migrations are complete, even if you skip step 3 above.

### Why migrations bypass PgBouncer

CivicSignals runs PgBouncer in **transaction mode** to cap backend connections (see
[architecture doc §4](../../06-architecture.md#4-database-choice)). Transaction mode
prohibits session-level commands like `SET SEARCH_PATH`, `CREATE TABLE`, `ALTER TABLE`,
and `LOCK TABLE` — all of which Alembic issues during DDL migrations.

The `migrate` service and the `init` service both use `DATABASE_DIRECT_URL` (Postgres
port 5432) instead of `DATABASE_URL` (PgBouncer port 6432). This is configured in
`apps/api/alembic/env.py`:

```python
_db_url = _settings.database_direct_url or _settings.database_url
```

Make sure `DATABASE_DIRECT_URL` points to `postgres:5432` (the container name on the
internal Docker network) in your `infra/.env`.

---

## Automatic migration on `up -d`

The prod compose file (`infra/docker-compose.yml`) includes a `migrate` one-shot
service. Every long-running app service declares:

```yaml
depends_on:
  migrate:
    condition: service_completed_successfully
```

This means:

- On a fresh `docker compose up -d`, migrations run automatically before any app
  process starts.
- On `docker compose up -d` after a `pull`, migrations run on the new image before the
  new containers replace the old ones.
- If migrations fail (exit non-zero), app containers are not started or restarted.
  Check logs with `docker compose logs migrate` and fix the issue before retrying.

This is the same pattern used in the dev compose (`infra/docker-compose.dev.yml`).

---

## Rollback

Alembic migrations are generally forward-only (downgrade scripts are not guaranteed to
be present for every revision). The recommended rollback strategy is:

1. Stop the stack.
2. Restore the database backup taken before the upgrade.
3. Switch back to the previous image tag in `infra/.env` (`CIVIC_VERSION=<previous>`).
4. Start the stack.

```bash
# Stop the stack (data volumes are untouched)
docker compose -f infra/docker-compose.yml --env-file infra/.env down

# Restore the database from backup
docker compose -f infra/docker-compose.yml --env-file infra/.env up -d postgres
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  exec -i postgres psql -U <POSTGRES_USER> <POSTGRES_DB> < backup-YYYYMMDDHHMMSS.sql

# Pin the previous version in infra/.env
# CIVIC_VERSION=0.3.1   # example — replace with the actual previous release tag

# Bring up the full stack on the old version
docker compose -f infra/docker-compose.yml --env-file infra/.env up -d
```

---

## Reading release notes

Release notes are published on GitHub at:
<https://github.com/CivicSignals/monorepo/releases>

Each release note follows this structure:

| Section | What to look for |
|---|---|
| **Breaking changes** | Config variable renames, removed endpoints, required manual steps |
| **Migration notes** | Any out-of-band SQL or data migrations you must run in addition to `alembic upgrade head` |
| **New required env vars** | Variables that must be added to `infra/.env` before starting the new version |
| **Deprecations** | Env vars or endpoints that will be removed in the next release |

**Before upgrading**, read the release notes for every version between your current
version and the target version (not just the latest). Check the
[compatibility matrix](#version-compatibility-matrix) for skipping rules.

---

## Version compatibility matrix

### Policy (pre-1.0)

CivicSignals is currently pre-1.0. The following policies apply:

- **No stability guarantees on minor version bumps (0.x → 0.y)** — database schemas,
  environment variable names, and API responses may change between minor releases.
  Always read the release notes.
- **Patch releases (0.x.y → 0.x.z)** are backward-compatible within the same minor
  series. Migrations are additive only (no column drops, no renames).
- **Do not skip minor versions** — upgrade one minor version at a time
  (e.g. 0.2 → 0.3, not 0.2 → 0.4). Some migrations depend on intermediate schema
  states.
- **No downgrade scripts are shipped** — roll back by restoring a database backup.

Once the project reaches 1.0, this policy will be replaced with a semantic-versioning
compatibility guarantee (major version bumps may include breaking changes; minor and
patch bumps are backward-compatible).

### Upgrade path table

The table below will be updated with each release. "Direct upgrade" means you can jump
from the source version to the target in a single `pull + migrate + up -d`. "Step
through" means you must upgrade to the intermediate version first.

| From | To | Upgrade path | Notes |
|---|---|---|---|
| 0.1.x | 0.2.x | Direct | — |
| 0.2.x | 0.3.x | Direct | — |
| 0.1.x | 0.3.x | Step through 0.2 | Do not skip 0.2 |
| _future_ | _future_ | _to be filled per release_ | — |

> **Pre-release / main branch:** if you are running a pre-release image (tagged
> `latest` or a commit SHA), upgrades are not guaranteed to be safe. Pin to a release
> tag (`CIVIC_VERSION=0.x.y`) for production.

### Environment variable compatibility

When upgrading, check the release notes for new required variables. A startup-time
validation error in the `api` or `migrate` container usually means a required variable
is missing from `infra/.env`. Compare `infra/.env` against `infra/.env.example` from
the new release to catch additions.

---

## Troubleshooting

### `migrate` container exits non-zero

```bash
docker compose -f infra/docker-compose.yml --env-file infra/.env logs migrate
```

Common causes:

- `DATABASE_DIRECT_URL` is wrong or Postgres is not yet healthy — wait and retry.
- The database backup was restored to an unexpected revision — check
  `alembic current` output inside the container.
- A migration script has a bug — file an issue at
  <https://github.com/CivicSignals/monorepo/issues>.

### App containers don't start after upgrade

If the `migrate` service fails, Docker Compose will not start the services that depend
on it. Fix the migration error first, then run:

```bash
docker compose -f infra/docker-compose.yml --env-file infra/.env up -d
```

### Checking the current schema revision

```bash
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  run --rm migrate \
  sh -c "alembic current"
```

### Checking which migrations are pending

```bash
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  run --rm migrate \
  sh -c "alembic history --indicate-current"
```
