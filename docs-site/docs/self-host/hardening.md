---
id: hardening
title: Hardening
sidebar_label: Hardening
slug: /self-host/hardening
---

# Hardening

This page covers the security baseline for a production self-hosted CivicSignals deployment. Read it alongside the [SECURITY.md](https://github.com/CivicSignals/monorepo/blob/main/SECURITY.md) disclosure policy and the [threat model](https://github.com/CivicSignals/monorepo/blob/main/security/threat-model.md) (§4.7) for the full picture.

## Firewall rules

Only two ports should be reachable from the internet:

| Port | Service | Notes |
|---|---|---|
| 80 | nginx (HTTP) | Redirect to 443 once TLS is configured |
| 443 | nginx (HTTPS) | Production traffic |
| 22 | SSH | Restrict to your IP if possible |

All other ports must be blocked at the host firewall. The `docker-compose.yml` already binds database and internal ports to the internal bridge network only — **nginx is the only service that publishes ports to the host**.

### UFW example (Ubuntu)

```bash
# Allow SSH (restrict to your IP in production)
ufw allow 22/tcp

# Allow HTTP and HTTPS
ufw allow 80/tcp
ufw allow 443/tcp

# Enable the firewall
ufw --force enable

# Verify
ufw status verbose
```

:::danger Never expose these ports
Do not expose ports **5432** (Postgres), **6432** (PgBouncer), **6379** (Redis), or **9000/9001** (MinIO) to the internet. The compose file binds these to the internal bridge network (`civicsignals_internal`) only.
:::

## Secrets management

### Change all development defaults before going live

The following variables have known weak defaults. Change **all of them** before exposing the stack to the internet:

```dotenv
# Generate a 64-byte hex secret:
# python3 -c "import secrets; print(secrets.token_hex(64))"
SECRET_KEY=<64-byte-random-hex>

# Use a strong password — 20+ characters, random
POSTGRES_PASSWORD=<strong-password>
DATABASE_URL=postgresql+asyncpg://civic:<strong-password>@pgbouncer:6432/civicsignals
DATABASE_DIRECT_URL=postgresql+asyncpg://civic:<strong-password>@postgres:5432/civicsignals

# MinIO / S3 credentials
S3_ACCESS_KEY_ID=<strong-key>
S3_SECRET_ACCESS_KEY=<strong-secret>

# Initial admin account (change password on first login)
CIVICSIGNALS_ADMIN_EMAIL=admin@your-domain.com
CIVICSIGNALS_ADMIN_PASSWORD=<strong-password>
```

See [Configuration reference](./configuration.md#secrets-that-must-change-for-production) for the full list.

### Protect the `.env` file

```bash
# Restrict access to root only
chmod 600 /opt/civicsignals/infra/.env
chown root:root /opt/civicsignals/infra/.env
```

The `.env` file contains all secrets. If an attacker can read it, the deployment is fully compromised.

### Advanced: use Docker secrets or a vault

For higher-security environments, consider externalizing secrets from the `.env` file:

- **Docker Swarm secrets** — mount secrets as files; read by the app at startup.
- **HashiCorp Vault** — use the Vault agent to inject secrets into the container environment.
- **AWS Secrets Manager / Parameter Store** — use an init container or sidecar to fetch secrets at startup.

For Kubernetes deployments, see the [Kubernetes / Helm guide](./kubernetes.md#secrets-management) for Sealed Secrets and External Secrets Operator options.

## Non-root container users

The official CivicSignals images run as a non-root user (`civic`, UID 1000). Do not override this with `user: root` in your compose file unless absolutely necessary for a volume-permission workaround.

To verify:

```bash
docker compose -f infra/docker-compose.yml --env-file infra/.env \
  exec api whoami
# Should print: civic
```

## Redis authentication

The default compose configuration does not set a Redis password (acceptable on the internal bridge network). For additional defense-in-depth in production:

1. Add a `requirepass <strong-password>` directive to a custom `redis.conf`.
2. Update `REDIS_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND` in `infra/.env` to include the password: `redis://:password@redis:6379/0`.

## TLS

Enable TLS before serving real traffic. See [Production — TLS with Let's Encrypt](./production.md#tls-with-lets-encrypt) for the full procedure. The nginx configuration shipped in `infra/nginx/nginx.conf` already includes the security headers baseline:

```nginx
add_header X-Content-Type-Options  nosniff        always;
add_header X-Frame-Options         SAMEORIGIN     always;
add_header Referrer-Policy         strict-origin-when-cross-origin always;
```

Once TLS is active, add HSTS:

```nginx
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

## Backups

Enable automated backups before receiving real data. A single data loss event cannot be undone. See [Backup & restore](./backup.md) for the full procedure.

At a minimum:

1. Run nightly `pg_dump` and ship the output off-site.
2. Enable Redis AOF persistence (already enabled in the default compose: `--appendonly yes --appendfsync everysec`).
3. Configure MinIO replication or back up the `miniodata` volume to an external S3 bucket.

## Container image verification

Official CivicSignals images are signed with [cosign](https://docs.sigstore.dev/cosign/overview/). Verify before pulling in a high-security environment:

```bash
cosign verify ghcr.io/civicsignals/api:<version> \
  --certificate-identity=https://github.com/CivicSignals/monorepo/.github/workflows/release-images.yml@refs/heads/main \
  --certificate-oidc-issuer=https://token.actions.githubusercontent.com
```

Pin to a specific release tag (`CIVIC_VERSION=0.x.y`) rather than `latest` in `infra/.env`. Immutable tags prevent supply-chain substitution attacks.

## Keeping dependencies up to date

- Subscribe to [GitHub Security Advisories](https://github.com/CivicSignals/monorepo/security/advisories) for CivicSignals-specific vulnerability notifications.
- Enable [Dependabot alerts](https://docs.github.com/en/code-security/dependabot) on your fork if you maintain a custom fork.
- Run `docker compose pull` and upgrade regularly — see [Upgrade guide](./upgrade.md).

## Related documents

- [SECURITY.md](https://github.com/CivicSignals/monorepo/blob/main/SECURITY.md) — vulnerability disclosure policy and security contact
- [Threat model](https://github.com/CivicSignals/monorepo/blob/main/security/threat-model.md) — STRIDE analysis, trust boundaries, data classification, and known residual risks
- [Backup & restore](./backup.md) — database backup procedures
- [Production deployment](./production.md) — TLS, nginx configuration, process types
