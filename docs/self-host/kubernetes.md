# Self-hosting CivicSignals on Kubernetes

<!-- TODO Q4: expand into a full operator guide -->

This document is a stub.  A complete Kubernetes self-host guide is planned for Q4.

## Quick start (evaluation — bundled datastores)

> **Warning:** bundled datastores are suitable for evaluation only.
> Use managed Postgres (RDS), Redis (ElastiCache), and S3 for production.

```bash
# 1. Add the bitnami repo (bundled datastores use bitnami subcharts)
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

# 2. Clone the repo and build chart dependencies
git clone https://github.com/CivicSignals/monorepo.git
cd monorepo
helm dependency update infra/helm/civicsignals

# 3. Install with bundled datastores
#    With release name "civicsignals" the rendered Service names are:
#      Postgres:  civicsignals-postgresql:5432
#      Redis:     civicsignals-redis-master:6379
#      MinIO:     civicsignals-minio:9000
#      PgBouncer: civicsignals-pgbouncer:6432
#    (civicsignals.fullname = "civicsignals" when release name contains chart name)
helm upgrade --install civicsignals infra/helm/civicsignals \
  --namespace civicsignals \
  --create-namespace \
  --set bundled.postgres.enabled=true \
  --set bundled.redis.enabled=true \
  --set bundled.minio.enabled=true \
  --set secrets.databaseUrl="postgresql+asyncpg://civicsignals:civicsignals@civicsignals-pgbouncer:6432/civicsignals" \
  --set secrets.databaseDirectUrl="postgresql+asyncpg://civicsignals:civicsignals@civicsignals-postgresql:5432/civicsignals" \
  --set secrets.celeryBrokerUrl="redis://civicsignals-redis-master:6379/1" \
  --set secrets.celeryResultBackend="redis://civicsignals-redis-master:6379/2" \
  --set secrets.redisUrl="redis://civicsignals-redis-master:6379/0" \
  --set config.s3EndpointUrl="http://civicsignals-minio:9000" \
  --set secrets.s3AccessKeyId="civicsignals" \
  --set secrets.s3SecretAccessKey="civicsignals" \
  --set secrets.pgbouncerDbPassword="civicsignals" \
  --set secrets.secretKey="$(openssl rand -hex 32)" \
  --set ingress.web.host=civicsignals.example.com \
  --set ingress.api.host=api.civicsignals.example.com
```

## Production (external managed datastores)

For production, point the chart at your managed Postgres (RDS), Redis
(ElastiCache), and S3 bucket.  No bundled subcharts are needed.

```bash
helm upgrade --install civicsignals infra/helm/civicsignals \
  --namespace civicsignals \
  --create-namespace \
  --set secrets.databaseUrl="postgresql+asyncpg://user:pass@your-pgbouncer:6432/civicsignals" \
  --set secrets.databaseDirectUrl="postgresql+asyncpg://user:pass@your-rds.cluster.us-east-1.rds.amazonaws.com:5432/civicsignals" \
  --set secrets.celeryBrokerUrl="redis://your-elasticache.cache.amazonaws.com:6379/1" \
  --set secrets.celeryResultBackend="redis://your-elasticache.cache.amazonaws.com:6379/2" \
  --set secrets.redisUrl="redis://your-elasticache.cache.amazonaws.com:6379/0" \
  --set secrets.s3AccessKeyId="AK..." \
  --set secrets.s3SecretAccessKey="..." \
  --set config.s3RawBucket="civicsignals-prod" \
  --set config.s3Region="us-east-1" \
  --set secrets.secretKey="$(openssl rand -hex 32)" \
  --set ingress.web.host=civicsignals.example.com \
  --set ingress.api.host=api.civicsignals.example.com
```

## Single-host ingress (api + web on same domain)

```bash
helm upgrade --install civicsignals infra/helm/civicsignals \
  --set ingress.web.host=civicsignals.example.com \
  --set ingress.api.host=civicsignals.example.com \
  --set ingress.api.path=/api
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
