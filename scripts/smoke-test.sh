#!/usr/bin/env bash
# Smoke tests — curl probes against the running platform.
# Each probe prints a labelled pass/fail line.
# Exits 0 if all probes pass; exits 1 if any probe fails.
#
# Usage:
#   make smoke                          # uses defaults
#   SMOKE_API_KEY=<key> make smoke      # authenticated probes
set -uo pipefail

KONG="${KONG_BASE_URL:-http://localhost:8080}"
KONG_ADMIN="${KONG_ADMIN_URL:-http://localhost:8001}"
SMOKE_API_KEY="${SMOKE_API_KEY:-}"
TIMEOUT=5

pass=0
fail=0

ok()    { printf '[PASS]    %s\n' "$*"; pass=$(( pass + 1 )); }
fail()  { printf '[FAIL]    %s\n' "$*" >&2; fail=$(( fail + 1 )); }

probe() {
    local label="$1"
    local url="$2"
    local expected_status="${3:-200}"
    shift 3
    # remaining args forwarded as extra curl flags (e.g. -H "Authorization: Bearer ...")

    local actual_status
    actual_status=$(curl -s -o /dev/null -w '%{http_code}' \
        --max-time "$TIMEOUT" "$@" "$url" 2>/dev/null || echo "000")

    if [[ "$actual_status" == "$expected_status" ]]; then
        ok "${label} (HTTP ${actual_status})"
    else
        fail "${label} — expected HTTP ${expected_status}, got ${actual_status} (${url})"
    fi
}

# ── Probes ────────────────────────────────────────────────────────────────────

printf '\nSmoke tests against %s\n\n' "$KONG"

# Kong admin is reachable (localhost only)
probe "Kong admin reachable"    "${KONG_ADMIN}/status"  200

# Unauthenticated /v1/models must return 401 (key-auth enforced)
probe "LiteLLM /v1/models — unauthenticated" "${KONG}/v1/models" 401

# Authenticated /v1/models returns 200 with a valid key
if [[ -n "$SMOKE_API_KEY" ]]; then
    probe "LiteLLM /v1/models via Kong — authenticated" \
        "${KONG}/v1/models" 200 \
        -H "Authorization: Bearer ${SMOKE_API_KEY}"
else
    printf '[SKIP]    LiteLLM /v1/models — authenticated (SMOKE_API_KEY not set)\n'
fi

# LiteLLM health endpoint proxied through Kong (no auth required on /health)
probe "LiteLLM /v1/health via Kong"  "${KONG}/v1/health"  200

# ── Result ────────────────────────────────────────────────────────────────────

printf '\n%d passed, %d failed\n\n' "$pass" "$fail"

if [[ $fail -gt 0 ]]; then
    exit 1
fi
