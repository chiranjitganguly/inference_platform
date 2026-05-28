#!/usr/bin/env bash
# Smoke tests — curl probes against the running platform.
# Each probe prints a labelled pass/fail line.
# Exits 0 if all probes pass; exits 1 if any probe fails.
set -uo pipefail

KONG="${KONG_BASE_URL:-http://localhost:8080}"
KONG_ADMIN="${KONG_ADMIN_URL:-http://localhost:8001}"
TIMEOUT=5

pass=0
fail=0

ok()   { printf '[PASS]    %s\n' "$*"; pass=$(( pass + 1 )); }
fail() { printf '[FAIL]    %s\n' "$*" >&2; fail=$(( fail + 1 )); }

probe() {
    local label="$1"
    local url="$2"
    local expected_status="${3:-200}"

    local actual_status
    actual_status=$(curl -s -o /dev/null -w '%{http_code}' \
        --max-time "$TIMEOUT" "$url" 2>/dev/null || echo "000")

    if [[ "$actual_status" == "$expected_status" ]]; then
        ok "${label} (HTTP ${actual_status})"
    else
        fail "${label} — expected HTTP ${expected_status}, got ${actual_status} (${url})"
    fi
}

# ── Probes ────────────────────────────────────────────────────────────────────

printf '\nSmoke tests against %s\n\n' "$KONG"

# Kong proxy is reachable
probe "Kong proxy reachable"           "${KONG}/health"           200

# Kong admin is reachable (localhost only)
probe "Kong admin reachable"           "${KONG_ADMIN}/status"     200

# LiteLLM models list proxied through Kong (requires seed-kong)
probe "LiteLLM /v1/models via Kong"   "${KONG}/v1/models"        200

# LiteLLM health endpoint proxied through Kong
probe "LiteLLM /v1/health via Kong"   "${KONG}/v1/health"        200

# ── Result ────────────────────────────────────────────────────────────────────

printf '\n%d passed, %d failed\n\n' "$pass" "$fail"

if [[ $fail -gt 0 ]]; then
    exit 1
fi
