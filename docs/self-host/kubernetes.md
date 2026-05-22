# Self-hosting CivicSignals on Kubernetes

<!-- TODO Q4: expand into a full operator guide -->

This document is a stub.  A complete Kubernetes self-host guide is planned for Q4.

## Quick start (evaluation)

```bash
# 1. Add the bitnami repo (bundled datastores use bitnami subcharts)
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

# 2. Clone the repo and build chart dependencies
git clone https://github.com/CivicSignals/monorepo.git
cd monorepo
helm dependency update infra/helm/civicsignals

# 3. Install with bundled datastores (NOT for production)
helm upgrade --install civicsignals infra/helm/civicsignals \
  --namespace civicsignals \
  --create-namespace \
  --set bundled.postgres.enabled=true \
  --set bundled.redis.enabled=true \
  --set bundled.minio.enabled=true \
  --set secrets.secretKey="$(openssl rand -hex 32)" \
  --set ingress.web.host=civicsignals.example.com \
  --set ingress.api.host=api.civicsignals.example.com
```

## Production (external managed datastores)

For production, point the chart at your managed Postgres (RDS), Redis
(ElastiCache), and S3 bucket:

```bash
helm upgrade --install civicsignals infra/helm/civicsignals \
  --namespace civicsignals \
  --create-namespace \
  --set secrets.databaseUrl="postgresql+asyncpg://user:pass@your-pgbouncer:6432/civicsignals" \
  --set secrets.databaseDirectUrl="postgresql+psycopg://user:pass@your-rds:5432/civicsignals" \
  --set secrets.redisUrl="redis://your-elasticache:6379/0" \
  --set secrets.s3AccessKeyId="AK..." \
  --set secrets.s3SecretAccessKey="..." \
  --set config.s3Bucket="civicsignals-prod" \
  --set config.s3Region="us-east-1" \
  --set secrets.secretKey="$(openssl rand -hex 32)" \
  --set ingress.web.host=civicsignals.example.com \
  --set ingress.api.host=api.civicsignals.example.com
```

## Secrets management

The chart creates a Kubernetes Secret from `values.yaml`.  In production,
use one of:

- **Sealed Secrets** (`kubeseal`) — commit encrypted secrets to git.
- **External Secrets Operator** — sync from AWS Secrets Manager / Vault.

See `infra/helm/civicsignals/templates/secret.yaml`.

## Upgrading

```bash
helm upgrade civicsignals infra/helm/civicsignals [--values my-values.yaml]
```

The Alembic migration Job runs automatically as a pre-upgrade Helm hook.

## Process types

CivicSignals uses one container image (`ghcr.io/civicsignals/api`) for six
process types, selected by `docker-entrypoint.sh`:

| Process | Helm component | Scalable? |
|---|---|---|
| `api` | `api` Deployment | Yes (HPA enabled by default) |
| `worker_ingest` | `workerIngest` Deployment | Yes |
| `worker_extract` | `workerExtract` Deployment | Yes |
| `worker_score` | `workerScore` Deployment | Yes |
| `worker_notify` | `workerNotify` Deployment | Yes |
| `scheduler` | `scheduler` Deployment | **No** — singleton only |

The scheduler **must** run as a singleton (Celery beat).  The chart enforces
`replicas: 1` and `strategy: Recreate`.  Redis-based leader election is
planned for a future release (TODO D14).

## SPDX-License-Identifier: AGPL-3.0-only
