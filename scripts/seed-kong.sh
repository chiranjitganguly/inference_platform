#!/usr/bin/env bash
# Seed Kong — verify the smoke-test consumer is active and print the API key.
# Kong runs in DB-less (declarative) mode; all config lives in services/kong/kong.yml.
# The smoke-test consumer and its key are injected from SMOKE_API_KEY at Kong startup.
set -euo pipefail

KONG_ADMIN="${KONG_ADMIN_URL:-http://localhost:8001}"

ok()   { printf '[OK]      %s\n' "$*"; }
info() { printf '[INFO]    %s\n' "$*"; }
err()  { printf '[ERROR]   %s\n' "$*" >&2; }

wait_for_kong() {
    local attempts=0
    until curl -sf "${KONG_ADMIN}/status" >/dev/null 2>&1; do
        attempts=$(( attempts + 1 ))
        if [[ $attempts -ge 30 ]]; then
            err "Kong admin API not reachable at ${KONG_ADMIN} after 30 attempts."
            exit 1
        fi
        printf '[INFO]    Waiting for Kong (%d/30)...\n' "$attempts"
        sleep 2
    done
    ok "Kong admin API is reachable."
}

verify_consumer() {
    local consumer
    consumer=$(curl -sf "${KONG_ADMIN}/consumers/smoke-test-consumer" 2>/dev/null || echo "")
    if [[ -z "$consumer" ]]; then
        err "smoke-test-consumer not found in Kong."
        err "Ensure SMOKE_API_KEY is set in .env and the core stack is running."
        exit 1
    fi
    ok "Consumer: smoke-test-consumer is registered."
}

print_key() {
    local key="${SMOKE_API_KEY:-}"
    if [[ -z "$key" ]]; then
        err "SMOKE_API_KEY is not set in the environment."
        err "Add it to .env (e.g. SMOKE_API_KEY=my-local-dev-key) and restart: make restart svc=kong"
        exit 1
    fi
    printf '\n'
    ok "Smoke-test API key is configured."
    printf '\nTo use it:\n'
    printf '  export SMOKE_API_KEY=%s\n' "$key"
    printf '  curl -s http://localhost:8080/v1/models -H "Authorization: Bearer %s" | jq .\n' "$key"
    printf '\n'
}

# ── Main ─────────────────────────────────────────────────────────────────────

main() {
    printf '\nVerifying Kong seed at %s\n\n' "$KONG_ADMIN"
    wait_for_kong
    verify_consumer
    print_key
    printf 'Kong seed verification complete.\n'
}

main "$@"
