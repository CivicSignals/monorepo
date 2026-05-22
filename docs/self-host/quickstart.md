# Self-host quickstart

> **Task O2 seed** — this is the minimal quickstart for single-host deployments.
> Full production hardening, upgrade paths, config reference, backup, and monitoring
> are covered in the complete self-host docs (task Q4).
> <!-- TODO Q4: expand into the full docs/self-host/ section -->

CivicSignals ships as a Docker Compose stack. A fresh single-host install takes
about five minutes on any Linux server with Docker installed.

## Prerequisites

| Requirement | Minimum |
|---|---|
| OS | Linux (Ubuntu 22.04 LTS recommended) |
| RAM | 4 GB (8 GB recommended) |
| Disk | 20 GB free |
| Docker Engine | 24+ with Compose V2 (`docker compose`) |
| Open ports | 80, 443 (HTTP/HTTPS) |

Install Docker: <https://docs.docker.com/engine/install/>

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

> **First login:** use the email and password you set in `CIVICSIGNALS_ADMIN_EMAIL`
> and `CIVICSIGNALS_ADMIN_PASSWORD`.
> Admin user creation requires task **B1** (auth) to be complete. Until then,
> the `init` command verifies database connectivity and runs migrations; the admin
> user creation step will print a TODO reminder and exit 0.

## What's in the stack

| Service | Role | Persistent volume |
|---|---|---|
| `postgres` | Primary database (Postgres 16 + pgvector) | `pgdata` |
| `pgbouncer` | Connection pool (transaction mode, doc 06 §4) | — |
| `redis` | Celery broker + cache (AOF persistence) | `redisdata` |
| `minio` | S3-compatible object storage (raw documents, exports) | `miniodata` |
| `minio-init` | One-shot: creates the raw-documents bucket | — |
| `api` | FastAPI HTTP server | — |
| `worker_ingest` | Celery worker — ingestion queue | — |
| `worker_extract` | Celery worker — extraction queue | — |
| `worker_score` | Celery worker — scoring queue | — |
| `worker_notify` | Celery worker — notification queue | — |
| `scheduler` | Celery beat (singleton scheduler) | — |
| `web` | Next.js frontend | — |
| `nginx` | Reverse proxy (HTTP on port 80) | — |

## Using external S3 instead of MinIO

If you have an AWS S3 bucket (or Cloudflare R2, Backblaze B2, etc.), you can skip
MinIO entirely:

1. In `infra/.env`, set:
   ```
   S3_ENDPOINT_URL=           # leave empty for native AWS S3
   S3_ACCESS_KEY_ID=<your key>
   S3_SECRET_ACCESS_KEY=<your secret>
   S3_RAW_BUCKET=<your bucket name>
   S3_REGION=us-east-1        # adjust as needed
   ```
2. In `infra/docker-compose.yml`, comment out or remove the `minio` and `minio-init`
   services and their volume reference under `volumes:`.

## Stopping and upgrading

```bash
# Stop the stack (data is preserved in volumes)
docker compose -f infra/docker-compose.yml --env-file infra/.env down

# Upgrade to a new release
docker compose -f infra/docker-compose.yml --env-file infra/.env pull
docker compose -f infra/docker-compose.yml --env-file infra/.env up -d
docker compose -f infra/docker-compose.yml --env-file infra/.env run --rm init
```

The `init` command is idempotent — Alembic skips already-applied migrations,
and the seed step detects existing data.

## Adding TLS (Let's Encrypt)

<!-- TODO Q4: expand TLS section with certbot + nginx config snippet -->

TLS hardening via certbot + Let's Encrypt is tracked in tasks A4/A5 and will
be documented in the full self-host guide (Q4). The `certdata` volume in
`docker-compose.yml` is already mounted read-only into nginx at
`/etc/letsencrypt` so the certificates will be picked up automatically once
certbot populates it.

Short version:

```bash
# Install certbot on the host (not inside Docker)
apt install -y certbot
certbot certonly --standalone -d your-domain.com

# Then edit infra/nginx/nginx.conf:
# - Add a server block on port 443 with ssl_certificate directives
# - Change the port-80 server block to redirect to 443
docker compose -f infra/docker-compose.yml --env-file infra/.env restart nginx
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

<!-- TODO Q4: link to full self-host docs once Q4 is done -->

- **Configuration reference:** `docs/self-host/config.md` (task O4)
- **Upgrade guide:** `docs/self-host/upgrade.md` (task O5)
- **Backup and DR:** `docs/self-host/backup.md` (task LC-8)
- **Community support:** [GitHub Discussions](https://github.com/CivicSignals/monorepo/discussions) (task LC-17)
