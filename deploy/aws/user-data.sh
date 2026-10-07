#!/bin/bash
# EC2 first-boot setup for Ubuntu 24.04 (docs/deployment/aws.md). Run once by cloud-init as root:
# paste this whole file into "Advanced details -> User data" when launching the instance, or let
# deploy/aws/cloudformation.yaml fetch and run it.
#
# Adds swap, sets the time zone to IST, installs Docker, clones the repo, writes a starter .env
# (only if none exists) and pre-builds the image, so after filling in .env one
# `docker compose up -d` starts the stack. Safe to re-run.
#
# Progress: /var/log/ai-ind-setup.log   Done: ~/setup-done.txt (in the app user's home)
set -uo pipefail

REPO_URL="${REPO_URL:-https://github.com/Tophunt-max/Ai-IND-money-.git}"
BRANCH="${BRANCH:-main}"
APP_USER="${APP_USER:-ubuntu}"
SWAP_GIB="${SWAP_GIB:-2}"

HOME_DIR="$(getent passwd "$APP_USER" | cut -d: -f6)"
APP_DIR="$HOME_DIR/Ai-IND-money-"
exec > >(tee -a /var/log/ai-ind-setup.log) 2>&1
step() { echo "=== $(date '+%F %T') $*"; }

step "swap (${SWAP_GIB} GiB)"
if [ "$SWAP_GIB" -gt 0 ] && ! swapon --show | grep -q /swapfile; then
    fallocate -l "${SWAP_GIB}G" /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
echo 'vm.swappiness=20' > /etc/sysctl.d/99-swap.conf && sysctl -q --system

step "time zone Asia/Kolkata"
timedatectl set-timezone Asia/Kolkata

step "docker"
if ! command -v docker >/dev/null; then
    curl -fsSL https://get.docker.com | sh || { echo "docker install failed"; exit 1; }
fi
systemctl enable --now docker
usermod -aG docker "$APP_USER"

step "repo $REPO_URL ($BRANCH) -> $APP_DIR"
if [ ! -d "$APP_DIR/.git" ]; then
    sudo -u "$APP_USER" git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR" || { echo "clone failed"; exit 1; }
fi
if [ ! -f "$APP_DIR/.env" ]; then
    install -m 600 -o "$APP_USER" -g "$APP_USER" "$APP_DIR/deploy/aws/env.example" "$APP_DIR/.env"
    # The instance's public IP, for SKOPAQ_EXPECTED_EGRESS_IP (the Elastic IP once attached).
    tok="$(curl -fsS -X PUT http://169.254.169.254/latest/api/token \
        -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null || true)"
    ip="$(curl -fsS -H "X-aws-ec2-metadata-token: $tok" \
        http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
    echo "public IPv4 now: ${ip:-unknown} (set SKOPAQ_EXPECTED_EGRESS_IP to the Elastic IP)"
fi

step "pre-building the image (10-20 min on t4g.small)"
build="ok"
(cd "$APP_DIR" && sudo -u "$APP_USER" docker compose build api) || build="FAILED (re-run: docker compose build api)"

echo "SETUP-DONE $(date '+%F %T') image build: $build" > "$HOME_DIR/setup-done.txt"
chown "$APP_USER:$APP_USER" "$HOME_DIR/setup-done.txt"
step "done (image build: $build). Next: fill in $APP_DIR/.env, then docker compose up -d"
