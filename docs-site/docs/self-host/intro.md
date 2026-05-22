---
id: intro
title: Self-host Overview
sidebar_label: Overview
slug: /self-host/intro
---

# Self-hosting CivicSignals

CivicSignals is fully open-source (AGPL-3.0) and designed to be self-hosted. The production stack is the same `docker-compose` configuration used by the CivicSignals team — there is no cloud-only lock-in.

## Stack overview

The self-host stack runs six process types from a single Docker image (`ghcr.io/civicsignals/api`), selected by `docker-entrypoint.sh`:

| Process | Role |
|---|---|
| `api` | uvicorn — the FastAPI HTTP server |
| `worker_ingest` | Celery — ingestion queue (fetches URLs per recipe) |
| `worker_extract` | Celery — extraction + LLM queue |
| `worker_score` | Celery — signal scoring queue |
| `worker_notify` | Celery — notifications queue |
| `scheduler` | Celery Beat — cron scheduler (singleton) |

Supporting infrastructure: **Postgres 16 + pgvector**, **Redis**, **MinIO** (S3-compatible object storage), **PgBouncer** (connection pooling in transaction mode), **nginx** (reverse proxy, TLS termination).

## Documentation in this section

| Page | What it covers |
|---|---|
| [Quickstart](./quickstart.md) | 3-command Docker Compose install on a single Linux server |
| [Production](./production.md) | Server sizing, TLS with Let's Encrypt, nginx configuration, the six process types, PgBouncer connection modes |
| [Configuration](./configuration.md) | Every environment variable, scope, default, and production requirements |
| [Upgrade](./upgrade.md) | How to pull new images and run Alembic migrations safely; version compatibility matrix |
| [Hardening](./hardening.md) | Firewall rules, secrets management, non-root containers, TLS, Redis auth |
| [Backup & Restore](./backup.md) | `pg_dump` procedures, off-site shipping with rclone, restore steps, PITR notes |
| [Kubernetes / Helm](./kubernetes.md) | Helm chart quick start (bundled datastores) and production (external managed datastores), scaling, secrets management |

## Source files

The infrastructure lives in the [`infra/`](https://github.com/CivicSignals/monorepo/tree/main/infra) directory of the monorepo:

- `infra/docker-compose.yml` — production single-host compose file
- `infra/.env.example` — environment variable template (copy to `infra/.env`)
- `infra/nginx/nginx.conf` — nginx reverse proxy configuration
- `infra/helm/civicsignals/` — Helm chart for Kubernetes deployments
