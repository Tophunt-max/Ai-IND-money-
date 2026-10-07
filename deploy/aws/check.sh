#!/usr/bin/env bash
# Readiness checks for the AWS EC2 host (docs/deployment/aws.md). Run on the host:
#     bash deploy/aws/check.sh
# One line per check (PASS | WARN | FAIL); exits 1 if any check FAILs. Secret values in .env are
# only checked for presence, never printed.
set -u
cd "$(dirname "$0")/../.." || exit 1

P=0 W=0 F=0
pass() { printf 'PASS  %s: %s\n' "$1" "$2"; P=$((P + 1)); }
warn() { printf 'WARN  %s: %s\n' "$1" "$2"; W=$((W + 1)); }
fail() { printf 'FAIL  %s: %s\n' "$1" "$2"; F=$((F + 1)); }
done_() { echo; echo "Summary: $P passed, $W warnings, $F failed"; [ "$F" -eq 0 ]; exit $?; }
has_key() { grep -Eq "^$1=[\"']?[^[:space:]\"']" .env 2>/dev/null; }
env_val() { sed -n "s/^$1=//p" .env 2>/dev/null | tail -n 1 | tr -d "\r\"'" | xargs; }

echo "== Host"
mem_mib=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
swap_mib=$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$mem_mib" -ge 1800 ]; then pass "memory" "${mem_mib} MiB"; else warn "memory" "${mem_mib} MiB (2 GiB or more recommended)"; fi
if [ $((mem_mib + swap_mib)) -ge 3500 ]; then pass "swap" "${swap_mib} MiB"
else warn "swap" "${swap_mib} MiB: RAM + swap under ~3.5 GiB, analyses may run out of memory"; fi
tz="$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null)"
if [ "$tz" = Asia/Kolkata ]; then pass "time zone" "$tz"; else warn "time zone" "${tz:-?} (containers use IST anyway; host logs are easier in IST)"; fi
free_gib=$(df -Pk . | awk 'NR == 2 {print int($4/1048576)}')
if [ "$free_gib" -ge 8 ]; then pass "disk" "${free_gib} GiB free"; else warn "disk" "${free_gib} GiB free: run docker image prune -f"; fi
if docker info >/dev/null 2>&1; then pass "docker" "$(docker --version | cut -d, -f1)"
else fail "docker" "not reachable (not in the docker group? log out and back in)"; done_; fi

echo; echo "== AWS"
tok="$(curl -fsS -m 2 -X PUT http://169.254.169.254/latest/api/token \
    -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null)"
md() { curl -fsS -m 2 -H "X-aws-ec2-metadata-token: $tok" "http://169.254.169.254/latest/meta-data/$1" 2>/dev/null; }
if [ -n "$tok" ]; then
    pass "instance" "$(md instance-type) in $(md placement/region)"
    public_ip="$(md public-ipv4)"
else
    warn "instance" "instance metadata not reachable (not on EC2?)"
    public_ip=""
fi
egress_ip="$(curl -fsS -m 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]')"
expected="$(env_val SKOPAQ_EXPECTED_EGRESS_IP)"
if [ -z "$expected" ]; then
    warn "egress IP" "${egress_ip:-?}; set SKOPAQ_EXPECTED_EGRESS_IP in .env to the Elastic IP"
elif [ "$egress_ip" = "$expected" ]; then
    pass "egress IP" "$egress_ip = SKOPAQ_EXPECTED_EGRESS_IP (whitelist this one at INDstocks)"
else
    fail "egress IP" "${egress_ip:-?} != SKOPAQ_EXPECTED_EGRESS_IP $expected (Elastic IP not associated?)"
fi
if [ -n "$public_ip" ] && [ -n "$egress_ip" ] && [ "$public_ip" != "$egress_ip" ]; then
    warn "public IP" "instance $public_ip, traffic leaves as $egress_ip (a proxy or NAT?)"
fi

echo; echo "== Configuration"
if [ ! -f .env ]; then fail ".env" "missing: cp deploy/aws/env.example .env and fill it in"; done_; fi
perm="$(stat -c %a .env)"
if [ "$perm" = 600 ]; then pass ".env" "present, mode 600"; else warn ".env" "mode $perm: chmod 600 .env"; fi
if has_key SKOPAQ_GOOGLE_API_KEY || has_key GOOGLE_API_KEY; then pass "Gemini key" "set"; else fail "Gemini key" "set SKOPAQ_GOOGLE_API_KEY"; fi
if has_key SKOPAQ_SUPABASE_URL && has_key SKOPAQ_SUPABASE_SERVICE_KEY; then pass "Supabase" "URL and service key set"
else warn "Supabase" "not set: no shared kill switch, P&L history or memory"; fi
telegram=0
if has_key SKOPAQ_TELEGRAM_BOT_TOKEN; then telegram=1; pass "Telegram" "token set"
else warn "Telegram" "no token: run only api and scheduler (docker compose up -d api scheduler)"; fi
tm="$(env_val SKOPAQ_TRADING_MODE)" sm="$(env_val SKOPAQ_SCHEDULER_MODE)"
if [ "${tm:-paper}" = live ] || [ "${sm:-paper}" = live ]; then
    warn "mode" "trading=${tm:-paper} scheduler=${sm:-paper}: LIVE places real orders"
else
    pass "mode" "trading=${tm:-paper} scheduler=${sm:-paper}"
fi

echo; echo "== Stack"
services="api scheduler"; [ "$telegram" = 1 ] && services="$services telegram"
api_up=0
for svc in $services; do
    cid="$(docker compose ps -q "$svc" 2>/dev/null)"
    state="$( [ -n "$cid" ] && docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$cid" 2>/dev/null || echo "not running")"
    if [ "$state" = running/healthy ]; then pass "$svc" "$state"; [ "$svc" = api ] && api_up=1
    else fail "$svc" "$state (docker compose up -d $svc; docker compose logs --tail 50 $svc)"; fi
done
if [ "$api_up" = 1 ]; then
    port="$(docker compose port api 8000 2>/dev/null)"
    case "$port" in 127.0.0.1:*) pass "api port" "$port (loopback only)" ;; *) fail "api port" "${port:-?}: expected 127.0.0.1:8000" ;; esac
    if curl -fsS -m 5 http://127.0.0.1:8000/health >/dev/null; then pass "api /health" "answers"; else fail "api /health" "no answer"; fi
    if [ -n "$public_ip" ] && curl -fsS -m 3 "http://$public_ip:8000/health" >/dev/null 2>&1; then
        fail "api exposure" "reachable from the internet at $public_ip:8000: close port 8000 in the security group"
    fi
    if out="$(docker compose exec -T api skopaq status 2>&1)"; then pass "skopaq status" "ok"
    else fail "skopaq status" "$(printf '%s' "$out" | tail -n 1)"; fi
fi
done_
