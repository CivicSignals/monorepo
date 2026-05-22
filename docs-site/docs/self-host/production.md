---
id: production
title: Production Deployment
sidebar_label: Production
slug: /self-host/production
---

# Production deployment

This page covers hardening a single-host Docker Compose deployment for production use. It picks up where the [quickstart](./quickstart.md) leaves off.

## Recommended server sizing

| Tier | vCPU | RAM | Disk | Suitable for |
|---|---|---|---|---|
| Small (dev/evaluation) | 2 | 4 GB | 40 GB SSD | < 500 signals/day |
| Recommended (Phase 0) | 4 | 8 GB | 80 GB SSD | up to ~5,000 signals/day |
| Larger single-host | 8 | 16 GB | 160 GB SSD | heavier workloads before moving to Kubernetes |

The [staging deploy playbook](https://github.com/CivicSignals/monorepo/blob/main/docs/self-host/staging-deploy.md) uses a **Hetzner CX32** (4 vCPU, 8 GB RAM, 80 GB SSD). Equivalent options: Vultr, DigitalOcean, OVH.

## Provisioning

```bash
# One-time provisioning script (installs Docker, certbot, sets up systemd unit)
curl -fsSL https://raw.githubusercontent.com/CivicSignals/monorepo/main/infra/provision/provision.sh | \
  DOMAIN=app.example.com \
  CERTBOT_EMAIL=ops@example.com \
  DEPLOY_DIR=/opt/civicsignals \
  bash
```

This script:
- Installs Docker Engine + Compose V2
- Installs certbot and obtains a Let's Encrypt certificate for your domain
- Creates `/opt/civicsignals/` with the correct permissions
- Enables a systemd unit so the stack restarts on host reboot

## TLS with Let's Encrypt

The `certdata` named volume is already mounted read-only into nginx at `/etc/letsencrypt`. Once certbot populates it, certificates are picked up automatically.

### Obtain the initial certificate

```bash
# Install certbot on the host (not inside Docker)
apt install -y certbot

# Temporarily stop nginx so certbot's standalone mode can bind port 80
docker compose -f infra/docker-compose.yml --env-file infra/.env stop nginx

certbot certonly --standalone -d app.example.com --email ops@example.com --agree-tos

# Restart nginx
docker compose -f infra/docker-compose.yml --env-file infra/.env start nginx
```

### Configure nginx for HTTPS

Edit `infra/nginx/nginx.conf` to add a port-443 server block and redirect HTTP to HTTPS:

```nginx
server {
    listen 80 default_server;
    server_name app.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name app.example.com;

    ssl_certificate     /etc/letsencrypt/live/app.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/app.example.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    client_max_body_size 50m;

    location ~ ^/(api|docs|redoc|openapi\.json|healthz) {
        proxy_pass http://api_upstream;
        proxy_http_version 1.1;
        proxy_set_header Connection         "";
        proxy_set_header Host               $host;
        proxy_set_header X-Real-IP          $remote_addr;
        proxy_set_header X-Forwarded-For    $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto  $scheme;
    }

    location / {
        proxy_pass http://web_upstream;
        proxy_http_version 1.1;
        proxy_set_header Connection         "";
        proxy_set_header Host               $host;
        proxy_set_header X-Real-IP          $remote_addr;
        proxy_set_header X-Forwarded-For    $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto  $scheme;
    }
}
```

Then reload nginx:

```bash
docker compose -f infra/docker-compose.yml --env-file infra/.env restart nginx
```

### Auto-renew certificates

Certbot's systemd timer (installed by `apt install certbot`) handles renewal automatically. After renewal, nginx needs a reload to pick up the new certificate:

```bash
# Add to /etc/letsencrypt/renewal-hooks/deploy/civicsignals.sh
#!/bin/bash
docker compose -f /opt/civicsignals/infra/docker-compose.yml \
  --env-file /opt/civicsignals/infra/.env \
  exec nginx nginx -s reload
```

```bash
chmod +x /etc/letsencrypt/renewal-hooks/deploy/civicsignals.sh
```

## The six process types

CivicSignals runs six process types from a single Docker image (`ghcr.io/civicsignals/api`), selected by the `command` field in `docker-compose.yml` which is passed to `apps/api/docker-entrypoint.sh`:

| Process | Queue | Description | Scalable? |
|---|---|---|---|
| `api` | — | FastAPI/uvicorn HTTP server | Yes (put behind a load balancer) |
| `worker_ingest` | `ingest` | Celery — fetches URLs per recipe | Yes |
| `worker_extract` | `extract` | Celery — LLM extraction pipeline | Yes |
| `worker_score` | `score` | Celery — signal scoring + dedup | Yes |
| `worker_notify` | `notify` | Celery — email/Slack/webhook delivery | Yes |
| `scheduler` | — | Celery beat — cron scheduler | **No — singleton only** |

:::warning Scheduler singleton
The `scheduler` (Celery beat) **must** run as exactly one instance. Running more than one beat process causes duplicate task scheduling. On Kubernetes, the Helm chart enforces `replicas: 1` with `strategy: Recreate`. On single-host Compose, the service runs as a single container automatically.
:::

The `worker_ingest` service uses a slightly heavier image (`ghcr.io/civicsignals/api-worker`) that includes Playwright and Chromium for rendering JavaScript-heavy pages.

## PgBouncer: transaction mode vs. direct connection

Two database URLs serve different purposes:

| URL | Port | Pool mode | Used by |
|---|---|---|---|
| `DATABASE_URL` | 6432 (PgBouncer) | Transaction | All API requests and Celery tasks |
| `DATABASE_DIRECT_URL` | 5432 (Postgres) | None | Alembic migrations, `LISTEN/NOTIFY`, long export jobs |

**Why two URLs?** PgBouncer in transaction mode caps the number of backend Postgres connections regardless of how many app processes are running. This is critical for high worker concurrency. However, transaction mode does not allow session-level state across query boundaries (`SET SEARCH_PATH`, advisory locks, prepared transactions, `LISTEN/NOTIFY`). Alembic migrations and the Celery beat scheduler use `DATABASE_DIRECT_URL` to bypass PgBouncer.

In `infra/.env`:

```dotenv
# Application traffic (via PgBouncer)
DATABASE_URL=postgresql+asyncpg://civic:<POSTGRES_PASSWORD>@pgbouncer:6432/civicsignals

# Migrations and LISTEN/NOTIFY (direct to Postgres)
DATABASE_DIRECT_URL=postgresql+asyncpg://civic:<POSTGRES_PASSWORD>@postgres:5432/civicsignals
```

Both `DATABASE_URL` and `DATABASE_DIRECT_URL` are **required** in production. If `DATABASE_DIRECT_URL` is unset, Alembic falls back to `DATABASE_URL`, which may cause DDL migration failures.

## Environment variables to set for production

At minimum, change these from their development defaults before exposing the stack to the internet:

| Variable | Why |
|---|---|
| `POSTGRES_PASSWORD` | Default `civic` is well-known |
| `DATABASE_URL` | Must embed the new password |
| `DATABASE_DIRECT_URL` | Must embed the new password |
| `SECRET_KEY` | Default `dev-only-change-me` allows JWT forgery |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | Default `civic`/`civic-secret` are well-known |
| `CIVIC_BASE_URL` | Set to your public domain (e.g. `https://app.example.com`) |
| `NEXT_PUBLIC_API_BASE_URL` | Set to `https://app.example.com/api/v1` |
| `WEB_BASE_URL` | Set to `https://app.example.com` (used in email links) |
| `SMTP_HOST` | Set to your production SMTP provider |

Generate a strong `SECRET_KEY`:

```bash
python3 -c "import secrets; print(secrets.token_hex(64))"
```

See [Configuration reference](./configuration.md) for the full list.

## Keeping the stack running

```bash
# Check service health
docker compose -f infra/docker-compose.yml --env-file infra/.env ps

# Tail all logs
docker compose -f infra/docker-compose.yml --env-file infra/.env logs -f

# Tail a specific service
docker compose -f infra/docker-compose.yml --env-file infra/.env logs -f api
```

The compose file sets `restart: unless-stopped` on all long-running services, so they come back after a host reboot. If you use the provisioning script, a systemd unit handles the initial `docker compose up -d` on host start.

## Optional: observability stack

An optional `--profile observability` adds Grafana, Prometheus, Loki, Promtail, Tempo, and the OpenTelemetry Collector:

```bash
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  --profile observability up -d
```

Grafana is available on `http://your-server:3100` (override with `GRAFANA_PORT`). See `docs/self-host/observability.md` for full setup.

## Next steps

- [Configuration reference](./configuration.md) — every environment variable
- [Upgrade guide](./upgrade.md) — how to pull new releases safely
- [Hardening](./hardening.md) — firewall, secrets rotation, non-root users
- [Backup & restore](./backup.md) — automated database backups
