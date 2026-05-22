# Staging deploy — Phase 0 single-VPS playbook

> **Task A4** — automation for the Phase-0 staging environment (doc 06 §10).
> Production (A5) uses the same compose stack on a separate VPS; only DNS and
> the GitHub secret names differ.

## Overview

Every merge to `main` (after CI passes) triggers **`.github/workflows/deploy.yml`**,
which SSH-connects to the staging VPS and runs `infra/deploy/deploy.sh`.  The
script pulls the new images, runs Alembic migrations, restarts containers, and
does a health check; it rolls back to the previous image tag on failure.

PRs that touch `apps/api` also get an Alembic migration SQL preview posted as
a collapsible PR comment (from `.github/workflows/migration-preview.yml`).

```
git push → main
  └─ CI (ci.yml) passes
       └─ deploy.yml (workflow_run)
            └─ SSH → infra/deploy/deploy.sh (on VPS)
                  ├─ docker compose pull
                  ├─ docker compose run --rm init   ← Alembic migrate
                  ├─ docker compose up -d
                  └─ curl /healthz (retry → rollback on failure)
```

---

## External steps — what a human must do

The automation is complete; the following are one-time manual steps that
require a real VPS and DNS, which cannot be performed in CI.

### 1. Provision a VPS

Recommended: **Hetzner CX32** (4 vCPU, 8 GB RAM, 80 GB SSD, €13/mo) or
equivalent at Vultr, DigitalOcean, or OVH.

- OS: Ubuntu 22.04 LTS
- Open inbound ports: 22 (SSH), 80 (HTTP), 443 (HTTPS)
- Note the public IPv4 address

### 2. Point DNS

Create an `A` record for `staging.civicsignals.io` (or your subdomain) pointing
at the VPS IP.  Propagation takes 1–60 minutes.

Verify: `dig +short staging.civicsignals.io` should return the VPS IP.

### 3. Run the provisioning script

SSH in as root and run:

```bash
curl -fsSL https://raw.githubusercontent.com/CivicSignals/monorepo/main/infra/provision/provision.sh | \
  DOMAIN=staging.civicsignals.io \
  CERTBOT_EMAIL=ops@civicsignals.io \
  DEPLOY_DIR=/opt/civicsignals \
  bash
```

This script (idempotent, safe to re-run) does:
- Installs Docker Engine + Compose plugin
- Configures UFW firewall (SSH + 80 + 443 only)
- Creates a 2 GB swapfile
- Creates the `civicsignals` deploy user
- Creates `/opt/civicsignals/` app directory
- Installs certbot and obtains a Let's Encrypt cert for the domain
- Writes an nginx.conf with TLS redirect
- Installs a `civicsignals.service` systemd unit (auto-start on boot)
- Installs the nightly `pg_dump → Backblaze B2` backup cron

### 4. Generate and install the deploy SSH key

On any machine (or GitHub Codespaces):

```bash
ssh-keygen -t ed25519 -C "civicsignals-staging-deploy" -f /tmp/staging_deploy
# Prints public key:
cat /tmp/staging_deploy.pub
```

Copy the public key into the VPS:

```bash
ssh root@<VPS-IP> "echo '<public-key>' >> /home/civicsignals/.ssh/authorized_keys"
```

Keep the private key — you'll paste it into GitHub next.

### 5. Configure GitHub secrets

Go to: `https://github.com/CivicSignals/monorepo/settings/secrets/actions`

Add these **Repository secrets** (or set them on the `staging` **Environment**):

| Secret name | Value |
|---|---|
| `STAGING_SSH_HOST` | VPS IP address or hostname |
| `STAGING_SSH_USER` | `civicsignals` |
| `STAGING_SSH_KEY` | Contents of `/tmp/staging_deploy` (the private key) |
| `STAGING_DEPLOY_DIR` | `/opt/civicsignals` |

Optional (add later for Slack notifications):

| Secret name | Value |
|---|---|
| `STAGING_SLACK_WEBHOOK` | Slack incoming-webhook URL |

### 6. Push the compose file and .env to the VPS

Copy the files that CI will later keep in sync:

```bash
scp infra/docker-compose.yml civicsignals@<VPS-IP>:/opt/civicsignals/infra/docker-compose.yml
scp infra/nginx/nginx.conf   civicsignals@<VPS-IP>:/opt/civicsignals/infra/nginx/nginx.conf
```

Create `/opt/civicsignals/infra/.env` from the template:

```bash
scp infra/.env.example civicsignals@<VPS-IP>:/opt/civicsignals/infra/.env
ssh civicsignals@<VPS-IP> "nano /opt/civicsignals/infra/.env"
```

At minimum, set:

```bash
POSTGRES_USER=civic
POSTGRES_PASSWORD=<strong-random-password>
POSTGRES_DB=civicsignals
SECRET_KEY=<64-char-random-hex>          # python3 -c "import secrets;print(secrets.token_hex(32))"
CIVICSIGNALS_ADMIN_EMAIL=admin@example.com
CIVICSIGNALS_ADMIN_PASSWORD=<password>
S3_ACCESS_KEY_ID=<minio-or-s3-key>
S3_SECRET_ACCESS_KEY=<minio-or-s3-secret>
# ANTHROPIC_API_KEY / OPENAI_API_KEY as needed
```

### 7. First-time startup

```bash
ssh civicsignals@<VPS-IP>
cd /opt/civicsignals
docker compose -f infra/docker-compose.yml --env-file infra/.env up -d
docker compose -f infra/docker-compose.yml --env-file infra/.env run --rm init
```

Verify the stack is healthy:

```bash
docker compose -f infra/docker-compose.yml --env-file infra/.env ps
curl -sf https://staging.civicsignals.io/healthz | jq .
```

### 8. Trigger the first automated deploy

Push any commit to `main` (or manually trigger via the GitHub Actions UI):
`Actions → Deploy (staging) → Run workflow`.

---

## TLS via Let's Encrypt

The provisioning script handles the initial cert if `DOMAIN` and `CERTBOT_EMAIL`
are set.  To issue or renew manually:

```bash
# Issue (certbot standalone — stop nginx first if it's running on port 80)
docker compose -f infra/docker-compose.yml --env-file infra/.env stop nginx
certbot certonly --standalone --non-interactive --agree-tos \
  --email ops@civicsignals.io -d staging.civicsignals.io
docker compose -f infra/docker-compose.yml --env-file infra/.env start nginx

# Renewal (run from cron — certbot auto-detects when cert is near expiry)
certbot renew --quiet \
  --deploy-hook "docker compose -f /opt/civicsignals/infra/docker-compose.yml \
    --env-file /opt/civicsignals/infra/.env restart nginx"
```

The `certdata` Docker volume mounts `/etc/letsencrypt` read-only into the nginx
container.  The TLS-enabled `nginx.conf` (written by the provision script)
redirects port 80 → 443 and adds `Strict-Transport-Security`.

---

## Deploy workflow reference

### Automatic deploys

Every merge to `main` that makes CI green triggers a deploy automatically via
`workflow_run`.  No human action needed.

### Manual deploy

```
GitHub → Actions → Deploy (staging) → Run workflow
```

Choose an image tag (e.g. `sha-abc1234`) or leave blank for `latest`.

### Guards

- Deploy is **skipped** if `STAGING_SSH_HOST` secret is absent.  Contributors
  and fork PRs see a `::notice::` log message, not a failure.
- `workflow_run` only fires when the CI workflow **succeeds** (not on cancel or
  failure).

### Rollback

On health-check failure the deploy script automatically rolls back to the
previously running image tag and prints instructions to investigate logs.

Manual rollback:

```bash
ssh civicsignals@<VPS-IP>
cd /opt/civicsignals
CIVIC_VERSION=sha-<previous-sha> docker compose -f infra/docker-compose.yml \
  --env-file infra/.env pull --quiet
CIVIC_VERSION=sha-<previous-sha> docker compose -f infra/docker-compose.yml \
  --env-file infra/.env up -d
```

---

## Migration preview on PRs

When a PR touches `apps/api/`, the **`migration-preview.yml`** workflow:

1. Spins up a fresh Postgres 16 service container
2. Runs `alembic upgrade head --sql` to generate the pending DDL
3. Uploads the full SQL as a build artifact (retained 30 days)
4. Posts (or updates) a collapsible comment on the PR

This is **non-blocking** (`continue-on-error: true`) — a broken migration
preview never fails CI.

---

## Backup

The provision script installs `/usr/local/bin/civic-backup.sh`, which runs at
03:17 nightly via cron:

```
pg_dump → gzip → /tmp/civic-YYYYMMDD-HHMMSS.sql.gz → rclone → b2:civicsignals-backups/
```

To configure Backblaze B2:

```bash
apt-get install -y rclone
rclone config  # follow prompts; create a remote named "b2"
```

Local dumps older than 7 days are pruned automatically.

---

## Troubleshooting

```bash
# Tail logs
docker compose -f infra/docker-compose.yml --env-file infra/.env logs -f api

# Check service health
docker compose -f infra/docker-compose.yml --env-file infra/.env ps

# Re-run migrations manually
docker compose -f infra/docker-compose.yml --env-file infra/.env run --rm init

# Validate compose file
docker compose -f infra/docker-compose.yml config

# Inspect systemd unit
systemctl status civicsignals.service
journalctl -u civicsignals.service -f
```

---

## Recommended VPS sizing

| Phase | VPS | vCPU | RAM | SSD | Est. cost |
|---|---|---|---|---|---|
| Phase 0 staging | Hetzner CX32 | 4 | 8 GB | 80 GB | €13/mo |
| Phase 0 production | Hetzner CX42 | 8 | 16 GB | 160 GB | €27/mo |
| Phase 1 | AWS ECS + RDS | — | — | — | ~$200+/mo |

Graduate to Phase 1 when MRR ≥ $1k/mo or uptime SLAs require Multi-AZ
(doc 06 §10).
