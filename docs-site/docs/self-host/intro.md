---
id: intro
title: Self-host Overview
sidebar_label: Overview
slug: /self-host/intro
---

# Self-hosting CivicSignals

CivicSignals is fully open-source (AGPL-3.0) and designed to be self-hosted. The production stack is the same `docker-compose` configuration used in development — there's no cloud-only lock-in.

<!-- TODO Q4: Replace this stub with full self-host documentation. See task Q4 in TODO.md.
  Planned sections:
  - Quickstart (single VPS, docker-compose, 15 minutes to running)
  - Production deployment (Hetzner/DigitalOcean sizing, nginx, TLS, nightly backups)
  - Upgrade guide (how to pull new images and run migrations safely)
  - Hardening (firewall, least-privilege service accounts, Postgres auth)
  - Backup & restore (pg_dump + Backblaze B2 or S3, point-in-time recovery)
  - Configuration reference (all environment variables)
  Requires Q4 dependency tasks: O1 (signed images), O2 (docker-compose.yml quickstart).
-->

## What's coming (Q4)

Full self-host documentation is planned for **Q4 — Self-host docs**, after the following prerequisites land:

- **O1** — Multi-arch Docker images (amd64 + arm64), signed with cosign
- **O2** — `docker-compose.yml` quickstart with persistent volumes and seeded admin user

Once those are ready, Q4 will document:

1. **Quickstart** — get a running instance in 15 minutes on a single VPS
2. **Production deployment** — sizing, nginx reverse proxy, Let's Encrypt TLS
3. **Upgrade path** — pull new images, run migrations, verify health
4. **Hardening** — firewall rules, service account hardening, secrets management
5. **Backup & restore** — automated nightly backups, tested restore procedure

## Stack overview

The self-host stack runs six process types from a single Docker image, selected via `docker-entrypoint.sh`:

| Process | Role |
|---|---|
| `api` | uvicorn — the FastAPI HTTP server |
| `worker_ingest` | Celery — ingestion queue |
| `worker_extract` | Celery — extraction + LLM queue |
| `worker_score` | Celery — signal scoring queue |
| `worker_notify` | Celery — notifications queue |
| `scheduler` | Celery Beat — cron scheduler (singleton) |

Supporting services: **Postgres 16 + pgvector**, **Redis**, **MinIO** (S3-compatible), **PgBouncer** (connection pooling).

See the [infrastructure directory](https://github.com/CivicSignals/monorepo/tree/main/infra) for the full compose configuration.
