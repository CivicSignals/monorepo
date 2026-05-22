---
id: kubernetes
title: Kubernetes / Helm
sidebar_label: Kubernetes / Helm
slug: /self-host/kubernetes
---

# Kubernetes / Helm

CivicSignals ships a Helm chart at `infra/helm/civicsignals/`. This page explains how to deploy to Kubernetes for evaluation and for production.

## Prerequisites

- Kubernetes 1.28+ (EKS, GKE, AKS, k3s, etc.)
- Helm 3.x
- An ingress controller (nginx-ingress or equivalent) with cert-manager for TLS

## Quick start (evaluation — bundled datastores)

:::warning Evaluation only
Bundled datastores are suitable for evaluation only. Use managed Postgres (RDS), Redis (ElastiCache), and S3 for production.
:::

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

For production, point the chart at your managed Postgres (RDS), Redis (ElastiCache), and S3 bucket. No bundled subcharts are needed.

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

## Process types

CivicSignals uses one container image (`ghcr.io/civicsignals/api`) for six process types, selected by `docker-entrypoint.sh`. The Helm chart creates a separate Deployment for each:

| Process | Helm component | Scalable? |
|---|---|---|
| `api` | `api` Deployment | Yes (HPA enabled by default) |
| `worker_ingest` | `workerIngest` Deployment | Yes |
| `worker_extract` | `workerExtract` Deployment | Yes |
| `worker_score` | `workerScore` Deployment | Yes |
| `worker_notify` | `workerNotify` Deployment | Yes |
| `scheduler` | `scheduler` Deployment | **No — singleton only** |

:::warning Scheduler singleton
The `scheduler` (Celery beat) **must** run as exactly one instance. The chart enforces `replicas: 1` with `strategy: Recreate`. Do not scale this Deployment. Redis-based leader election is planned for a future release (task D14).
:::

## Migrations (Helm hook)

The chart includes a Kubernetes Job (`job-migrate.yaml`) that runs `alembic upgrade head` as a Helm **pre-upgrade** hook. This means:

- On `helm upgrade`, migrations run before the new Deployments roll out.
- If the migration Job fails, the upgrade is aborted and the old Deployments remain running.
- The Job connects via `DATABASE_DIRECT_URL` (bypassing PgBouncer transaction mode, just like the compose stack).

To run migrations manually:

```bash
helm upgrade civicsignals infra/helm/civicsignals [--values my-values.yaml]
# The pre-upgrade hook Job runs automatically.
```

Or trigger it directly:

```bash
kubectl create job --from=cronjob/civicsignals-migrate civicsignals-migrate-manual \
  -n civicsignals
kubectl wait --for=condition=complete job/civicsignals-migrate-manual -n civicsignals --timeout=120s
```

## Secrets management

The chart creates a Kubernetes Secret from values in `values.yaml`. In production, **do not pass secrets via `--set` on the command line** (they appear in shell history). Instead use one of:

### Sealed Secrets (`kubeseal`)

```bash
# Install kubeseal CLI + controller
helm repo add sealed-secrets https://bitnami-labs.github.io/sealed-secrets
helm install sealed-secrets-controller sealed-secrets/sealed-secrets -n kube-system

# Seal a secret
kubectl create secret generic civicsignals-secrets \
  --from-literal=secretKey="$(openssl rand -hex 32)" \
  --from-literal=databaseUrl="postgresql+asyncpg://..." \
  --dry-run=client -o yaml | \
  kubeseal --format yaml > sealed-secret.yaml

# Commit sealed-secret.yaml to your GitOps repo — it is safe to commit
kubectl apply -f sealed-secret.yaml
```

### External Secrets Operator

```bash
# Install ESO
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets -n external-secrets --create-namespace

# Create an ExternalSecret referencing AWS Secrets Manager, Vault, etc.
# See https://external-secrets.io/latest/provider-aws-secrets-manager/
```

See `infra/helm/civicsignals/templates/secret.yaml` for the Secret template structure.

## Horizontal scaling

All process types except `scheduler` support horizontal scaling. The chart ships with HPA (Horizontal Pod Autoscaler) definitions for the `api` and worker Deployments.

```bash
# Scale workers manually
kubectl scale deployment civicsignals-worker-extract -n civicsignals --replicas=4

# Or configure HPA target in values.yaml:
#   workerExtract:
#     autoscaling:
#       enabled: true
#       minReplicas: 2
#       maxReplicas: 10
#       targetCPUUtilizationPercentage: 70
```

## Using a values file

Rather than passing many `--set` flags, use a `values.yaml` override file:

```yaml
# my-values.yaml
secrets:
  secretKey: "<generated>"
  databaseUrl: "postgresql+asyncpg://user:pass@pgbouncer:6432/civicsignals"
  databaseDirectUrl: "postgresql+asyncpg://user:pass@rds:5432/civicsignals"
  celeryBrokerUrl: "redis://elasticache:6379/1"
  celeryResultBackend: "redis://elasticache:6379/2"
  redisUrl: "redis://elasticache:6379/0"
  s3AccessKeyId: "AK..."
  s3SecretAccessKey: "..."

config:
  s3RawBucket: "civicsignals-prod"
  s3Region: "us-east-1"
  llmDefaultProvider: "anthropic"

ingress:
  web:
    host: civicsignals.example.com
  api:
    host: api.civicsignals.example.com
```

```bash
helm upgrade --install civicsignals infra/helm/civicsignals \
  --namespace civicsignals \
  --create-namespace \
  --values my-values.yaml
```

## Upgrading

```bash
# Pull the latest chart and apply your values
helm upgrade civicsignals infra/helm/civicsignals --values my-values.yaml
```

The Alembic migration Job runs automatically as a pre-upgrade Helm hook. See the [Upgrade guide](./upgrade.md) for the general upgrade checklist — the Kubernetes flow replaces `docker compose pull/up -d` but the principles (backup first, check release notes, verify health after) are the same.

## Related documents

- [Quickstart](./quickstart.md) — single-host Docker Compose setup
- [Configuration reference](./configuration.md) — all environment variables
- [Upgrade guide](./upgrade.md) — upgrade checklist and compatibility matrix
- [Hardening](./hardening.md) — security baseline (firewall, secrets, TLS)
