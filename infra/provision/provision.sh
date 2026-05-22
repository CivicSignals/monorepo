#!/usr/bin/env bash
# Phase 0 VPS bootstrap — task A4 (doc 06 §10).
#
# One-shot host preparation for a fresh Ubuntu 22.04 LTS VPS (Hetzner CX32 or
# equivalent) running the single-host docker-compose stack.  Safe to re-run
# (idempotent).
#
# After this script, copy the compose file + a populated .env into $DEPLOY_DIR
# and then trigger a deploy (GitHub Actions does this automatically on merge to
# main once the STAGING_SSH_* secrets are configured).
#
# Usage (as root on the VPS):
#   curl -fsSL https://raw.githubusercontent.com/CivicSignals/monorepo/main/infra/provision/provision.sh | \
#     DOMAIN=staging.civicsignals.io DEPLOY_DIR=/opt/civicsignals bash
#
# Or clone and run locally on the VPS:
#   DOMAIN=staging.civicsignals.io DEPLOY_DIR=/opt/civicsignals ./provision.sh
#
# Variables:
#   DOMAIN       — FQDN that will serve traffic (used for TLS cert)  [required for TLS]
#   DEPLOY_DIR   — app directory on VPS                               [default: /opt/civicsignals]
#   DEPLOY_USER  — non-root OS user that CI SSHes as                  [default: civicsignals]
#   CERTBOT_EMAIL — email for Let's Encrypt registration              [required for TLS]
#   SKIP_TLS     — set to "1" to skip certbot (plain HTTP only)       [default: 0]
#
# SPDX-License-Identifier: AGPL-3.0-only
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/civicsignals}"
DEPLOY_USER="${DEPLOY_USER:-civicsignals}"
DOMAIN="${DOMAIN:-}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"
SKIP_TLS="${SKIP_TLS:-0}"

log()  { echo "[provision] $*"; }
warn() { echo "[provision:WARN] $*" >&2; }
err()  { echo "[provision:ERROR] $*" >&2; exit 1; }

require_root() {
  [ "$(id -u)" -eq 0 ] || err "Run as root (sudo -i or initial root login)."
}

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------
install_base() {
  log "Updating apt and installing base packages ..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y -qq
  apt-get install -y -qq \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    ufw \
    fail2ban \
    unattended-upgrades \
    cron \
    jq \
    git \
    python3-minimal
  log "Base packages installed."
}

# ---------------------------------------------------------------------------
# 2. Docker Engine + Compose plugin
# ---------------------------------------------------------------------------
install_docker() {
  if command -v docker >/dev/null 2>&1; then
    log "Docker already installed: $(docker --version)"
  else
    log "Installing Docker Engine ..."
    curl -fsSL https://get.docker.com | sh
    log "Docker Engine installed: $(docker --version)"
  fi
  systemctl enable --now docker
  # Add the deploy user to the docker group so CI can run compose without sudo.
  if id "${DEPLOY_USER}" >/dev/null 2>&1; then
    usermod -aG docker "${DEPLOY_USER}"
    log "Added ${DEPLOY_USER} to docker group."
  fi
}

# ---------------------------------------------------------------------------
# 3. Firewall
# ---------------------------------------------------------------------------
configure_firewall() {
  log "Configuring UFW firewall ..."
  ufw --force reset >/dev/null 2>&1
  ufw default deny incoming
  ufw default allow outgoing
  ufw allow OpenSSH
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw --force enable
  log "UFW enabled (SSH + HTTP + HTTPS)."
}

# ---------------------------------------------------------------------------
# 4. Swap (avoids OOM on small VPS during image pulls / LLM extraction)
# ---------------------------------------------------------------------------
configure_swap() {
  if swapon --show | grep -q '/swapfile'; then
    log "Swapfile already present."
    return 0
  fi
  log "Creating 2 GB swapfile ..."
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  # Tune swappiness: defer swap until memory pressure is real.
  echo "vm.swappiness=10" > /etc/sysctl.d/99-civicsignals.conf
  sysctl -p /etc/sysctl.d/99-civicsignals.conf >/dev/null 2>&1
  log "Swapfile ready."
}

# ---------------------------------------------------------------------------
# 5. Deploy user + SSH authorised_keys placeholder
# ---------------------------------------------------------------------------
setup_deploy_user() {
  if ! id "${DEPLOY_USER}" >/dev/null 2>&1; then
    log "Creating deploy user: ${DEPLOY_USER} ..."
    useradd -m -s /bin/bash "${DEPLOY_USER}"
  fi
  install -d -m 700 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" "/home/${DEPLOY_USER}/.ssh"
  touch "/home/${DEPLOY_USER}/.ssh/authorized_keys"
  chmod 600 "/home/${DEPLOY_USER}/.ssh/authorized_keys"
  chown "${DEPLOY_USER}:${DEPLOY_USER}" "/home/${DEPLOY_USER}/.ssh/authorized_keys"
  log "Deploy user '${DEPLOY_USER}' ready."
  log "ACTION REQUIRED: paste the CI deploy public key into:"
  log "  /home/${DEPLOY_USER}/.ssh/authorized_keys"
}

# ---------------------------------------------------------------------------
# 6. App directory
# ---------------------------------------------------------------------------
setup_deploy_dir() {
  install -d -m 755 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" "${DEPLOY_DIR}"
  install -d -m 755 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" "${DEPLOY_DIR}/infra"
  install -d -m 755 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" "${DEPLOY_DIR}/infra/nginx"
  log "Deploy directory: ${DEPLOY_DIR}"
}

# ---------------------------------------------------------------------------
# 7. certbot + Let's Encrypt TLS
# ---------------------------------------------------------------------------
install_certbot() {
  if [ "${SKIP_TLS}" = "1" ]; then
    warn "SKIP_TLS=1 — skipping certbot installation.  nginx will serve HTTP only."
    return 0
  fi
  if [ -z "${DOMAIN}" ]; then
    warn "DOMAIN not set — skipping certbot.  Set DOMAIN and CERTBOT_EMAIL then re-run."
    return 0
  fi
  if [ -z "${CERTBOT_EMAIL}" ]; then
    warn "CERTBOT_EMAIL not set — skipping certbot.  Set it and re-run."
    return 0
  fi

  log "Installing certbot ..."
  apt-get install -y -qq certbot

  # Use standalone mode to obtain the initial cert BEFORE the compose stack is
  # running (port 80 is free at this point in provisioning).
  log "Obtaining Let's Encrypt certificate for ${DOMAIN} ..."
  certbot certonly \
    --standalone \
    --non-interactive \
    --agree-tos \
    --email "${CERTBOT_EMAIL}" \
    -d "${DOMAIN}" \
    || warn "certbot failed — check DNS points to this server and port 80 is reachable."

  # Auto-renewal via cron (certbot ships a systemd timer too; belt-and-suspenders).
  ( crontab -l 2>/dev/null | grep -v certbot; \
    echo "0 3 * * * certbot renew --quiet --deploy-hook 'docker compose -f ${DEPLOY_DIR}/${COMPOSE_FILE:-infra/docker-compose.yml} --env-file ${DEPLOY_DIR}/infra/.env restart nginx'" ) | crontab -
  log "certbot renewal cron installed."
}

# ---------------------------------------------------------------------------
# 8. nginx TLS config (written to disk for compose volume-mount)
# ---------------------------------------------------------------------------
write_nginx_tls_conf() {
  if [ "${SKIP_TLS}" = "1" ] || [ -z "${DOMAIN}" ]; then
    log "Skipping nginx TLS config (TLS not requested or DOMAIN not set)."
    return 0
  fi

  local CONF_PATH="${DEPLOY_DIR}/infra/nginx/nginx.conf"
  if [ -f "${CONF_PATH}" ]; then
    log "nginx.conf already exists at ${CONF_PATH} — not overwriting."
    log "To enable TLS manually, see docs/self-host/staging-deploy.md §TLS."
    return 0
  fi

  log "Writing TLS-enabled nginx.conf to ${CONF_PATH} ..."
  cat > "${CONF_PATH}" << NGINX_CONF
# Reverse proxy — nginx (Phase 0, task A4).
# TLS via Let's Encrypt; certbot writes certs to /etc/letsencrypt (host path),
# which is mounted read-only into the nginx container as /etc/letsencrypt.
# SPDX-License-Identifier: AGPL-3.0-only

worker_processes auto;
pid /tmp/nginx.pid;

events {
  worker_connections 1024;
}

http {
  include       /etc/nginx/mime.types;
  default_type  application/octet-stream;

  log_format json_combined escape=json
    '{"time":"\$time_iso8601",'
    '"remote_addr":"\$remote_addr",'
    '"method":"\$request_method",'
    '"uri":"\$request_uri",'
    '"status":\$status,'
    '"bytes_sent":\$bytes_sent,'
    '"request_time":\$request_time,'
    '"upstream_addr":"\$upstream_addr",'
    '"upstream_response_time":"\$upstream_response_time"}';

  access_log /dev/stdout json_combined;
  error_log  /dev/stderr warn;

  sendfile on;
  tcp_nopush on;
  keepalive_timeout 65;
  gzip on;
  gzip_types text/plain text/css application/json application/javascript text/xml application/xml;

  add_header X-Content-Type-Options  nosniff        always;
  add_header X-Frame-Options         SAMEORIGIN     always;
  add_header Referrer-Policy         strict-origin-when-cross-origin always;

  limit_req_zone \$binary_remote_addr zone=auth:10m rate=5r/s;
  limit_req_zone \$binary_remote_addr zone=api:10m  rate=50r/s;

  upstream api_upstream {
    server api:8000;
    keepalive 16;
  }

  upstream web_upstream {
    server web:3000;
    keepalive 16;
  }

  # HTTP — redirect everything to HTTPS.
  server {
    listen 80 default_server;
    server_name ${DOMAIN};

    # Let certbot's ACME http-01 challenges through.
    location /.well-known/acme-challenge/ {
      root /var/www/certbot;
    }

    location / {
      return 301 https://\$host\$request_uri;
    }
  }

  # HTTPS
  server {
    listen 443 ssl http2 default_server;
    server_name ${DOMAIN};

    ssl_certificate     /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 10m;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;

    client_max_body_size 50m;

    location = /nginx-health {
      return 200 "OK\n";
      add_header Content-Type text/plain;
    }

    location ~ ^/(api|docs|redoc|openapi\\.json|healthz) {
      limit_req zone=api burst=100 nodelay;
      proxy_pass http://api_upstream;
      proxy_http_version 1.1;
      proxy_set_header Connection "";
      proxy_set_header Host               \$host;
      proxy_set_header X-Real-IP          \$remote_addr;
      proxy_set_header X-Forwarded-For    \$proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto  \$scheme;
      proxy_connect_timeout 10s;
      proxy_read_timeout    120s;
      proxy_send_timeout    30s;
    }

    location ~ ^/api/v[0-9]+/auth/ {
      limit_req zone=auth burst=10 nodelay;
      proxy_pass http://api_upstream;
      proxy_http_version 1.1;
      proxy_set_header Connection         "";
      proxy_set_header Host               \$host;
      proxy_set_header X-Real-IP          \$remote_addr;
      proxy_set_header X-Forwarded-For    \$proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto  \$scheme;
    }

    location / {
      proxy_pass http://web_upstream;
      proxy_http_version 1.1;
      proxy_set_header Connection         "";
      proxy_set_header Host               \$host;
      proxy_set_header X-Real-IP          \$remote_addr;
      proxy_set_header X-Forwarded-For    \$proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto  \$scheme;
      proxy_connect_timeout 10s;
      proxy_read_timeout    60s;
      proxy_send_timeout    30s;
    }
  }
}
NGINX_CONF
  chown "${DEPLOY_USER}:${DEPLOY_USER}" "${CONF_PATH}"
  log "nginx TLS config written."
}

# ---------------------------------------------------------------------------
# 9. systemd service — auto-start the compose stack on boot
# ---------------------------------------------------------------------------
install_systemd_unit() {
  local UNIT=/etc/systemd/system/civicsignals.service
  if [ -f "${UNIT}" ]; then
    log "Systemd unit already installed."
    return 0
  fi
  log "Installing systemd unit: ${UNIT} ..."
  cat > "${UNIT}" << UNIT_FILE
[Unit]
Description=CivicSignals compose stack
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${DEPLOY_USER}
WorkingDirectory=${DEPLOY_DIR}
ExecStart=/usr/bin/docker compose -f infra/docker-compose.yml --env-file infra/.env up --remove-orphans
ExecStop=/usr/bin/docker compose -f infra/docker-compose.yml --env-file infra/.env down
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
UNIT_FILE

  systemctl daemon-reload
  systemctl enable civicsignals.service
  log "systemd unit installed and enabled (will start on next boot after .env is in place)."
}

# ---------------------------------------------------------------------------
# 10. Nightly backup cron (pg_dump → Backblaze B2)
# ---------------------------------------------------------------------------
setup_backup_cron() {
  log "Installing nightly pg_dump backup cron ..."
  install -m 0755 /dev/stdin /usr/local/bin/civic-backup.sh << 'BACKUP'
#!/usr/bin/env bash
# Nightly pg_dump → Backblaze B2 (doc 06 §10).
# Requires: rclone configured with a remote named "b2" (run `rclone config`).
set -euo pipefail
DEPLOY_DIR="${DEPLOY_DIR:-/opt/civicsignals}"
COMPOSE_FILE="${COMPOSE_FILE:-infra/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-infra/.env}"
cd "${DEPLOY_DIR}"
TS=$(date +%Y%m%d-%H%M%S)
DUMP="/tmp/civic-${TS}.sql.gz"
docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" \
  exec -T postgres pg_dump \
    -U "${POSTGRES_USER:-civic}" \
    "${POSTGRES_DB:-civicsignals}" \
  | gzip > "${DUMP}"
rclone copy "${DUMP}" b2:civicsignals-backups/ \
  || echo "[backup:WARN] rclone not configured yet; dump saved locally at ${DUMP}"
# Retain 7 days of local dumps (safety net if B2 push fails).
find /tmp -name 'civic-*.sql.gz' -mtime +7 -delete
echo "[backup] Done: ${DUMP}"
BACKUP

  # Run at 03:17 nightly (prime to avoid thundering-herd with other crons).
  ( crontab -l 2>/dev/null | grep -v civic-backup.sh; \
    echo "17 3 * * * DEPLOY_DIR=${DEPLOY_DIR} /usr/local/bin/civic-backup.sh >> /var/log/civic-backup.log 2>&1" ) | crontab -
  log "Backup cron installed (03:17 daily). Configure rclone for B2 upload."
}

# ---------------------------------------------------------------------------
# 11. unattended-upgrades (security patches only)
# ---------------------------------------------------------------------------
configure_auto_updates() {
  if dpkg -l | grep -q unattended-upgrades; then
    log "Enabling unattended security upgrades ..."
    dpkg-reconfigure -plow unattended-upgrades 2>/dev/null || true
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  require_root
  install_base
  install_docker
  configure_firewall
  configure_swap
  setup_deploy_user
  setup_deploy_dir
  install_certbot
  write_nginx_tls_conf
  install_systemd_unit
  setup_backup_cron
  configure_auto_updates

  echo ""
  log "=== Provisioning complete ==="
  log ""
  log "Next steps (manual / one-time):"
  log "  1. Add the CI SSH public key to /home/${DEPLOY_USER}/.ssh/authorized_keys"
  log "     (generate with: ssh-keygen -t ed25519 -C 'civicsignals-staging-deploy')"
  log "     Put the private key in GitHub secret STAGING_SSH_KEY."
  log ""
  log "  2. Copy infra/docker-compose.yml to ${DEPLOY_DIR}/infra/docker-compose.yml"
  log "     Copy infra/nginx/nginx.conf  to ${DEPLOY_DIR}/infra/nginx/nginx.conf"
  log "     (CI's deploy.sh does this on each deploy once secrets are configured.)"
  log ""
  log "  3. Create ${DEPLOY_DIR}/infra/.env from infra/.env.example — fill all secrets."
  log ""
  log "  4. If TLS was skipped, see docs/self-host/staging-deploy.md §TLS for steps."
  log ""
  log "  5. Start the stack manually for the first time:"
  log "     sudo -u ${DEPLOY_USER} docker compose -f ${DEPLOY_DIR}/infra/docker-compose.yml --env-file ${DEPLOY_DIR}/infra/.env up -d"
  log "     sudo -u ${DEPLOY_USER} docker compose -f ${DEPLOY_DIR}/infra/docker-compose.yml --env-file ${DEPLOY_DIR}/infra/.env run --rm init"
  log ""
  log "  6. Configure GitHub secrets:"
  log "     STAGING_SSH_HOST    = <VPS IP or hostname>"
  log "     STAGING_SSH_USER    = ${DEPLOY_USER}"
  log "     STAGING_SSH_KEY     = <private key content>"
  log "     STAGING_DEPLOY_DIR  = ${DEPLOY_DIR}"
  log ""
  log "  7. Push to main — the deploy workflow triggers automatically."
  log ""
  log "See docs/self-host/staging-deploy.md for the full playbook."
}

main "$@"
