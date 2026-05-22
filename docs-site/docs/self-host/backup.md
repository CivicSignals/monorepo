---
id: backup
title: Backup & Restore
sidebar_label: Backup & Restore
slug: /self-host/backup
---

# Backup & restore

This page describes how to back up and restore a self-hosted CivicSignals instance. The primary data store is Postgres; Redis and MinIO also hold important state.

:::info Automated backup plumbing
Full automated backup scheduling (nightly cron, off-site shipping, retention policy) is being implemented as task **LC-8**. This page documents the manual procedures and the building blocks that LC-8 will automate.
:::

## What to back up

| Data | Store | Criticality | Recovery impact if lost |
|---|---|---|---|
| Application data (signals, workspaces, users, configs) | PostgreSQL `pgdata` volume | **Critical** | Total data loss |
| Celery task queue and cache | Redis `redisdata` volume | Medium | In-flight tasks lost; cache warms back up |
| Raw scraped documents and exports | MinIO `miniodata` volume | High | Raw documents lost; re-ingestion can recover most |
| TLS certificates | `certdata` volume | Low | Can be re-issued with certbot |

## Backing up PostgreSQL

### Manual `pg_dump`

```bash
# Replace values from your infra/.env
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  exec postgres pg_dump -U <POSTGRES_USER> <POSTGRES_DB> \
  | gzip > backup-$(date +%Y%m%d%H%M%S).sql.gz
```

The output is a gzip-compressed SQL dump. It is portable across Postgres versions.

### Restoring from a `pg_dump` backup

:::warning
Restoring overwrites all current data. Stop the application services first and confirm you are restoring to the correct database.
:::

```bash
# 1. Stop all app services (leave postgres running)
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  stop api worker_ingest worker_extract worker_score worker_notify scheduler web nginx

# 2. Drop and recreate the database
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  exec postgres psql -U <POSTGRES_USER> -c "DROP DATABASE IF EXISTS <POSTGRES_DB>;"
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  exec postgres psql -U <POSTGRES_USER> -c "CREATE DATABASE <POSTGRES_DB> OWNER <POSTGRES_USER>;"

# 3. Restore from the gzipped backup
gunzip -c backup-YYYYMMDDHHMMSS.sql.gz | \
  docker compose -f infra/docker-compose.yml --env-file infra/.env \
  exec -T postgres psql -U <POSTGRES_USER> <POSTGRES_DB>

# 4. Bring the full stack back up
docker compose -f infra/docker-compose.yml --env-file infra/.env up -d
```

## Shipping backups off-site

**Always store backups somewhere outside the server.** A single hardware failure or compromised host should not result in data loss.

### Using rclone to Backblaze B2 / S3 / Cloudflare R2

```bash
# Install rclone on the host
curl https://rclone.org/install.sh | bash

# Configure a remote (example: Backblaze B2)
rclone config
# Follow the interactive prompts to add a B2 (or S3) remote named "backup"

# Ship today's backup
BACKUP_FILE=backup-$(date +%Y%m%d%H%M%S).sql.gz

docker compose -f infra/docker-compose.yml --env-file infra/.env \
  exec postgres pg_dump -U <POSTGRES_USER> <POSTGRES_DB> \
  | gzip > "/tmp/$BACKUP_FILE"

rclone copy "/tmp/$BACKUP_FILE" backup:civicsignals-backups/postgres/
rm "/tmp/$BACKUP_FILE"
```

### Nightly cron backup (manual setup, pending LC-8 automation)

Until LC-8 lands, you can wire a simple nightly cron job:

```bash
# /opt/civicsignals/scripts/backup.sh
#!/bin/bash
set -euo pipefail

BACKUP_FILE="backup-$(date +%Y%m%d%H%M%S).sql.gz"
COMPOSE="docker compose -f /opt/civicsignals/infra/docker-compose.yml --env-file /opt/civicsignals/infra/.env"

source /opt/civicsignals/infra/.env

$COMPOSE exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
  | gzip > "/tmp/$BACKUP_FILE"

rclone copy "/tmp/$BACKUP_FILE" backup:civicsignals-backups/postgres/
rm "/tmp/$BACKUP_FILE"

echo "$(date): backup $BACKUP_FILE shipped"
```

```bash
chmod +x /opt/civicsignals/scripts/backup.sh

# Add to root's crontab (runs at 02:00 UTC every day)
crontab -l | { cat; echo "0 2 * * * /opt/civicsignals/scripts/backup.sh >> /var/log/civicsignals-backup.log 2>&1"; } | crontab -
```

## Point-in-time recovery (PITR)

The default Postgres setup in the compose file does not enable WAL archiving (Write-Ahead Log). This means the backup granularity is the `pg_dump` schedule — you can restore to any point at which a dump was taken, but not to an arbitrary moment in between.

For PITR capability, you need WAL archiving to an external store. This is on the roadmap as part of task LC-8. Until then, run `pg_dump` frequently (every 15–60 minutes) if your data loss tolerance is low.

## Backing up Redis

Redis persists to the `redisdata` volume using AOF (Append-Only File) mode, which is enabled in the default compose configuration (`--appendonly yes --appendfsync everysec`). This means Redis survives container restarts and host reboots.

For off-site Redis backup:

```bash
# Trigger a foreground BGSAVE inside Redis
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  exec redis redis-cli BGSAVE

# Copy the RDB dump file off-site
docker run --rm \
  --volumes-from $(docker compose -f infra/docker-compose.yml ps -q redis) \
  -v /tmp/redis-backup:/backup \
  alpine tar czf /backup/redis-$(date +%Y%m%d%H%M%S).tar.gz /data
```

Redis data is largely recoverable from Postgres if lost — the cache warms back up on the next requests, and Celery tasks are re-queued by the scheduler. The main risk is losing in-flight task state for tasks that were queued but not yet completed.

## Backing up MinIO

MinIO stores raw scraped HTML/PDFs and exported CSVs. If you lose this data, re-ingestion can recover most of it (at the cost of re-fetching URLs), but FOIA response PDFs that have since been taken down or redacted cannot be recovered.

### Using MinIO Client (mc)

```bash
# Mirror the MinIO bucket to a local directory
docker run --rm --network civicsignals_internal \
  -e MINIO_ROOT_USER=<S3_ACCESS_KEY_ID> \
  -e MINIO_ROOT_PASSWORD=<S3_SECRET_ACCESS_KEY> \
  -v /opt/civicsignals/minio-backup:/backup \
  minio/mc:latest sh -c "
    mc alias set local http://minio:9000 \$MINIO_ROOT_USER \$MINIO_ROOT_PASSWORD
    mc mirror --overwrite local/<S3_RAW_BUCKET> /backup/
  "
```

If you are using external S3 (not MinIO), configure S3 Lifecycle policies or S3 Cross-Region Replication on your bucket for durability and off-site copies.

## Backup retention

A sensible retention policy:

| Backup type | Frequency | Retention |
|---|---|---|
| Daily `pg_dump` | Nightly | 30 days |
| Weekly `pg_dump` | Weekly | 12 weeks |
| Pre-upgrade snapshot | Before every upgrade | Keep until next upgrade is stable |

Clean up old backups from your off-site store automatically using rclone's `--min-age` filter or your storage provider's lifecycle policies.

## Testing restores

A backup that has never been tested is not a backup. Test your restore procedure at least quarterly:

1. Spin up a separate test server or Docker environment.
2. Restore the backup following the steps above.
3. Run the stack health check: `curl http://test-server/healthz`.
4. Log in and verify that data looks correct.
5. Document the restore time — this is your Recovery Time Objective (RTO).

## Related documents

- [Upgrade guide](./upgrade.md) — always back up before upgrading
- [Hardening](./hardening.md) — access controls for backup files
- [Production deployment](./production.md) — server sizing and setup
