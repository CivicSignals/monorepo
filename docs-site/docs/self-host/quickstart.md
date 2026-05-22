---
id: quickstart
title: Quickstart
sidebar_label: Quickstart
slug: /self-host/quickstart
---

# Self-host quickstart

CivicSignals ships as a Docker Compose stack. A fresh single-host install takes about five minutes on any Linux server with Docker installed.

## Prerequisites

| Requirement | Minimum |
|---|---|
| OS | Linux (Ubuntu 22.04 LTS recommended) |
| RAM | 4 GB (8 GB recommended) |
| Disk | 20 GB free |
| Docker Engine | 24+ with Compose V2 (`docker compose`) |
| Open ports | 80, 443 (HTTP/HTTPS) |

Install Docker: https://docs.docker.com/engine/install/

## 3-command quickstart

```bash
# 1. Clone the repo (or download infra/ from a release tarball)
git clone https://github.com/CivicSignals/monorepo.git civicsignals
cd civicsignals

# 2. Configure: copy the env template and fill in your secrets
cp infra/.env.example infra/.env
$EDITOR infra/.env   # at minimum, set POSTGRES_PASSWORD, SECRET_KEY,
                     # CIVICSIGNALS_ADMIN_EMAIL, CIVICSIGNALS_ADMIN_PASSWORD,
                     # and your LLM provider key

# 3. Start the stack (detached) and run migrations + seed the admin account
docker compose -f infra/docker-compose.yml --env-file infra/.env up -d
docker compose -f infra/docker-compose.yml --env-file infra/.env run --rm init
```

The stack is now running. Open `http://your-server-ip/` to reach the app.

:::info First login
Use the email and password you set in `CIVICSIGNALS_ADMIN_EMAIL` and `CIVICSIGNALS_ADMIN_PASSWORD`.
:::

## What's in the stack

| Service | Role | Persistent volume |
|---|---|---|
| `postgres` | Primary database (Postgres 16 + pgvector) | `pgdata` |
| `pgbouncer` | Connection pool (transaction mode) | — |
| `redis` | Celery broker + cache (AOF persistence) | `redisdata` |
| `minio` | S3-compatible object storage (raw documents, exports) | `miniodata` |
| `minio-init` | One-shot: creates the raw-documents bucket | — |
| `migrate` | One-shot: runs Alembic migrations on every `up` | — |
| `init` | One-shot: migrations + admin seed (run explicitly) | — |
| `api` | FastAPI HTTP server | — |
| `worker_ingest` | Celery worker — ingestion queue | — |
| `worker_extract` | Celery worker — extraction queue | — |
| `worker_score` | Celery worker — scoring queue | — |
| `worker_notify` | Celery worker — notification queue | — |
| `scheduler` | Celery beat (singleton scheduler) | — |
| `web` | Next.js frontend | — |
| `nginx` | Reverse proxy (HTTP on port 80, HTTPS on port 443) | `certdata` |

## Using external S3 instead of MinIO

If you have an AWS S3 bucket (or Cloudflare R2, Backblaze B2, etc.), you can skip MinIO entirely:

1. In `infra/.env`, set:
   ```dotenv
   S3_ENDPOINT_URL=           # leave empty for native AWS S3
   S3_ACCESS_KEY_ID=<your key>
   S3_SECRET_ACCESS_KEY=<your secret>
   S3_RAW_BUCKET=<your bucket name>
   S3_REGION=us-east-1        # adjust as needed
   ```
2. In `infra/docker-compose.yml`, comment out or remove the `minio` and `minio-init` services and their volume reference under `volumes:`.

## Stopping and starting

```bash
# Stop the stack (data is preserved in named volumes)
docker compose -f infra/docker-compose.yml --env-file infra/.env down

# Start again
docker compose -f infra/docker-compose.yml --env-file infra/.env up -d
```

## Troubleshooting

```bash
# Check service status
docker compose -f infra/docker-compose.yml --env-file infra/.env ps

# Tail logs for a specific service
docker compose -f infra/docker-compose.yml --env-file infra/.env logs -f api

# Re-run migrations and seed manually
docker compose -f infra/docker-compose.yml --env-file infra/.env run --rm init

# Validate the compose file
docker compose -f infra/docker-compose.yml config
```

## Next steps

- **[Production deployment](./production.md)** — TLS, hardening, sizing
- **[Configuration reference](./configuration.md)** — all environment variables
- **[Upgrade guide](./upgrade.md)** — how to upgrade safely
- **[Hardening](./hardening.md)** — firewall, secrets, security baseline
- **[Backup & restore](./backup.md)** — `pg_dump`, PITR, off-site storage
- **[Kubernetes / Helm](./kubernetes.md)** — cluster deployment
