#!/usr/bin/env bash
# Deploy one commit of this repo on the EC2 host: run on the host by .github/workflows/deploy.yml
# over SSH (docs/deployment/github-auto-deploy.md), or by hand (from a copy: the checkout
# rewrites this file while it runs):
#     cp scripts/deploy/ec2-deploy.sh /tmp/ && bash /tmp/ec2-deploy.sh <commit-sha> [force]
#
# Exit codes: 0 deployed (or already at the commit), 3 skipped (market hours or a trading
# session is running), anything else failed (after rolling back to the previous commit when the
# new one does not come up healthy).
#
# Never restarts the stack while a session runs: SIGTERM makes the scheduler's session close its
# positions. `force` skips only the market-hours check, never the running-session check.
set -euo pipefail

SHA="${1:?usage: ec2-deploy.sh <commit-sha> [force]}"
FORCE="${2:-}"
APP_DIR="${APP_DIR:-$HOME/Ai-IND-money-}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-300}"

log() { echo "deploy: $*"; }

cd "$APP_DIR"
[ -f .env ] || { log ".env missing in $APP_DIR (create it first: docs/deployment/github-auto-deploy.md)"; exit 1; }

# 1. Market hours: Mon-Fri 08:30-16:00 IST covers the pre-flight (08:45), the session
#    (09:15-15:45) and the CLOSING grace period. Holidays are treated as trading days.
day="$(TZ=Asia/Kolkata date +%u)"
hm="$(TZ=Asia/Kolkata date +%H%M)"
if [ "$FORCE" != "force" ] && [ "$day" -le 5 ] && [ "$hm" -ge 0830 ] && [ "$hm" -lt 1600 ]; then
    log "market hours (IST $(TZ=Asia/Kolkata date '+%a %H:%M')): not deploying"
    exit 3
fi

# 2. A running session (scheduler's daemon/monitor child, or a one-off `compose run daemon`).
session_running() {
    if docker compose ps --status running --services 2>/dev/null | grep -x scheduler >/dev/null; then
        if ! docker compose exec -T scheduler python - <<'PY'
import os, sys
busy = []
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            args = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
    except OSError:
        continue
    if "skopaq.cli.main" in args:
        i = args.index("skopaq.cli.main")
        if i + 1 < len(args) and args[i + 1] in ("daemon", "monitor"):
            busy.append(" ".join(args))
print("\n".join(busy))
sys.exit(1 if busy else 0)
PY
        then
            return 0
        fi
    fi
    [ -n "$(docker ps -q --filter label=com.docker.compose.service=daemon)" ]
}
if session_running; then
    log "a trading session is running: not deploying"
    exit 3
fi

# 3. The commit.
git fetch --quiet origin
git cat-file -e "${SHA}^{commit}" 2>/dev/null || { log "commit $SHA not found on origin"; exit 1; }
prev="$(git rev-parse HEAD)"
target="$(git rev-parse "${SHA}^{commit}")"
if [ "$prev" = "$target" ] && [ "$FORCE" != "force" ]; then
    log "already at ${target:0:7}: nothing to do"
    exit 0
fi

# 4. Which services: the ones running now; else api + scheduler (+ telegram when a token is set).
services="${DEPLOY_SERVICES:-}"
if [ -z "$services" ]; then
    services="$(docker compose ps --status running --services 2>/dev/null \
        | grep -xE 'api|scheduler|telegram' | tr '\n' ' ' || true)"
fi
if [ -z "${services// /}" ]; then
    services="api scheduler"
    grep -qE '^SKOPAQ_TELEGRAM_BOT_TOKEN=.+' .env && services="$services telegram"
fi
services="$(echo "$services" | xargs)"

checkout() { git checkout --quiet -B main "$1" && git branch --quiet --set-upstream-to=origin/main main; }

wait_healthy() {
    local svc cid status deadline
    deadline=$(( $(date +%s) + HEALTH_TIMEOUT_SECONDS ))
    for svc in $services; do
        status=""
        while [ "$(date +%s)" -lt "$deadline" ]; do
            cid="$(docker compose ps -q "$svc")"
            status="$( [ -n "$cid" ] && docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo missing)"
            [ "$status" = "healthy" ] && break
            sleep 5
        done
        log "$svc: $status"
        if [ "$status" != "healthy" ]; then
            docker compose logs --tail 40 "$svc" || true
            return 1
        fi
    done
}

# shellcheck disable=SC2086  # $services is a word list
up() { docker compose build api && docker compose up -d --no-build $services && wait_healthy; }

log "deploying ${prev:0:7} -> ${target:0:7} (services: $services)"
checkout "$target"
if up; then
    docker image prune -f >/dev/null 2>&1 || true
    log "done: $(git log -1 --format='%h %s')"
    exit 0
fi

log "new commit did not come up healthy: rolling back to ${prev:0:7}"
checkout "$prev"
up || log "rollback did not come up healthy either: check 'docker compose ps' on the host"
exit 1
