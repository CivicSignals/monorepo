#!/usr/bin/env bash
# Phase 0 VPS bootstrap (doc 06 §10, TODO A4/A5).
#
# One-shot host prep for a fresh Debian/Ubuntu VPS (Hetzner CX32 or equivalent)
# that will run the single-host docker-compose stack. Idempotent — safe to
# re-run. After this, copy infra/docker-compose.yml + a populated .env into
# $DEPLOY_DIR and run `docker compose up -d` (CI does this via deploy/deploy.sh).
#
# Usage (as root, on the VPS):
#   DEPLOY_DIR=/opt/civicsignals ./provision.sh
#
# TLS: nginx runs as a compose service; issue Let's Encrypt certs with certbot
# (webroot or DNS-01) once DNS points at the box — not done here.
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/civicsignals}"

require_root() {
  [ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }
}

install_base() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y ca-certificates curl ufw fail2ban unattended-upgrades cron
}

install_docker() {
  if command -v docker >/dev/null 2>&1; then
    echo "docker already installed: $(docker --version)"
  else
    curl -fsSL https://get.docker.com | sh
  fi
  systemctl enable --now docker
}

configure_firewall() {
  ufw allow OpenSSH
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw --force enable
}

configure_swap() {
  # Small box: a swapfile avoids OOM during image builds / extraction spikes.
  if ! swapon --show | grep -q '/swapfile'; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
}

setup_backup_cron() {
  # Nightly pg_dump -> Backblaze B2 (doc 06 §10). Expects the stack running in
  # $DEPLOY_DIR and B2 configured via `rclone config` (remote name: b2).
  install -d "$DEPLOY_DIR"
  install -m 0755 /dev/stdin /usr/local/bin/civic-backup.sh <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$DEPLOY_DIR"
TS=\$(date +%Y%m%d-%H%M%S)
docker compose exec -T postgres pg_dump -U "\${POSTGRES_USER:-civic}" "\${POSTGRES_DB:-civicsignals}" \
  | gzip > "/tmp/civic-\${TS}.sql.gz"
rclone copy "/tmp/civic-\${TS}.sql.gz" b2:civicsignals-backups/ || echo "rclone not configured yet"
find /tmp -name 'civic-*.sql.gz' -mtime +2 -delete
EOF
  # 03:17 nightly, jittered off the hour.
  ( crontab -l 2>/dev/null | grep -v civic-backup.sh; echo "17 3 * * * /usr/local/bin/civic-backup.sh" ) | crontab -
}

main() {
  require_root
  install_base
  install_docker
  configure_firewall
  configure_swap
  setup_backup_cron
  echo "Provisioned. Next: place docker-compose.yml + .env in $DEPLOY_DIR, then deploy."
}

main "$@"
