#!/usr/bin/env bash
# Deploy one commit of this repo on the EC2 host: run on the host by .github/workflows/deploy.yml
# over SSH (docs/deployment/github-auto-deploy.md), or by hand (from a copy: the checkout
# rewrites this file while it runs):
#     cp scripts/deploy/ec2-deploy.sh /tmp/ && bash /tmp/ec2-deploy.sh <commit-sha> [force]
#
# NEW_ENV_FILE=<path> (set by the workflow from the ENV_FILE secret) replaces .env with that file
# (the old one is kept as .env.bak) and restarts the stack even when the commit is unchanged.
# The file is deleted when the script ends, whatever happens.
#
# Exit codes: 0 deployed (or nothing changed), 3 skipped (market hours or a trading session is
# running), anything else failed (after rolling back to the previous commit and .env when the new
# ones do not come up healthy).
#
# Never restarts the stack while a session runs: SIGTERM makes the scheduler's session close its
# positions. `force` skips only the market-hours check, never the running-session check.
set -euo pipefail

SHA="${1:?usage: ec2-deploy.sh <commit-sha> [force]}"
FORCE="${2:-}"
APP_DIR="${APP_DIR:-$HOME/Ai-IND-money-}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-300}"
NEW_ENV_FILE="${NEW_ENV_FILE:-}"

log() { echo "deploy: $*"; }
# shellcheck disable=SC2329  # run by the EXIT trap
cleanup() { if [ -n "$NEW_ENV_FILE" ]; then rm -f "$NEW_ENV_FILE"; fi; }
trap cleanup EXIT

cd "$APP_DIR"
if [ -z "$NEW_ENV_FILE" ] && [ ! -f .env ]; then
    log ".env missing in $APP_DIR: set the ENV_FILE secret or create it (docs/deployment/github-auto-deploy.md)"
    exit 1
fi

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

# 4. The .env from the ENV_FILE secret: CRLF from a pasted secret removed, mode 600.
env_changed=0
if [ -n "$NEW_ENV_FILE" ]; then
    [ -s "$NEW_ENV_FILE" ] || { log "NEW_ENV_FILE $NEW_ENV_FILE is missing or empty"; exit 1; }
    staged="$(mktemp "$APP_DIR/.env.new.XXXXXX")"
    tr -d '\r' < "$NEW_ENV_FILE" > "$staged"
    [ -z "$(tail -c 1 "$staged")" ] || echo >> "$staged"
    chmod 600 "$staged"
    if [ -f .env ] && [ "$(sha256sum < "$staged")" = "$(sha256sum < .env)" ]; then
        rm -f "$staged"
        log ".env unchanged"
    else
        note="first .env"
        if [ -f .env ]; then cp -p .env .env.bak && chmod 600 .env.bak && note="old one in .env.bak"; fi
        mv "$staged" .env
        env_changed=1
        log ".env updated from the ENV_FILE secret ($(grep -cE '^[A-Za-z_][A-Za-z0-9_]*=' .env) keys; $note)"
    fi
fi

if [ "$prev" = "$target" ] && [ "$env_changed" = 0 ] && [ "$FORCE" != "force" ]; then
    log "already at ${target:0:7}, .env unchanged: nothing to do"
    exit 0
fi

# 5. Services: api + scheduler, plus telegram when .env has its token (it exits without one).
#    DEPLOY_SERVICES overrides.
has_telegram() { grep -qE '^SKOPAQ_TELEGRAM_BOT_TOKEN=[^[:space:]]' .env; }
pick_services() {
    if [ -n "${DEPLOY_SERVICES:-}" ]; then
        services="$(echo "$DEPLOY_SERVICES" | xargs)"
    else
        services="api scheduler"
        if has_telegram; then services="$services telegram"; fi
    fi
}

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

# --force-recreate when .env changed: the containers read it only when they are created.
up() {
    local recreate=()
    if [ "$env_changed" = 1 ]; then recreate=(--force-recreate); fi
    pick_services
    case " $services " in
        *" telegram "*) ;;
        *) docker compose stop telegram >/dev/null 2>&1 || true ;;
    esac
    # shellcheck disable=SC2086  # $services is a word list
    docker compose build api && docker compose up -d --no-build "${recreate[@]}" $services && wait_healthy
}

pick_services
log "deploying ${prev:0:7} -> ${target:0:7}$( [ "$env_changed" = 1 ] && echo ' with new .env') (services: $services)"
checkout "$target"
if up; then
    rm -f .env.bak.failed
    docker image prune -f >/dev/null 2>&1 || true
    log "done: $(git log -1 --format='%h %s')"
    exit 0
fi

log "did not come up healthy: rolling back to ${prev:0:7}$( [ "$env_changed" = 1 ] && echo ' and the previous .env')"
if [ "$env_changed" = 1 ] && [ -f .env.bak ]; then
    cp -p .env .env.bak.failed
    cp -p .env.bak .env
fi
checkout "$prev"
up || log "rollback did not come up healthy either: check 'docker compose ps' on the host"
exit 1
