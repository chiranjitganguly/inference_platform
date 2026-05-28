#!/usr/bin/env bash
# Seed Kong with all platform routes and services.
# Idempotent — uses PUT (upsert) for every entity; safe to re-run.
set -euo pipefail

KONG_ADMIN="${KONG_ADMIN_URL:-http://localhost:8001}"

ok()  { printf '[OK]      %s\n' "$*"; }
err() { printf '[ERROR]   %s\n' "$*" >&2; }

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

# PUT a Kong entity — idempotent create-or-update by name.
# Usage: put_entity <resource-path> <json-body>
put_entity() {
    local path="$1"
    local body="$2"
    local response
    response=$(curl -sf -X PUT \
        -H 'Content-Type: application/json' \
        -d "$body" \
        "${KONG_ADMIN}${path}" 2>&1) || {
        err "Failed to PUT ${path}: ${response}"
        exit 1
    }
}

# ── Services ──────────────────────────────────────────────────────────────────

seed_services() {
    put_entity "/services/litellm-proxy" '{
        "name": "litellm-proxy",
        "url":  "http://guardrails:8088"
    }'
    ok "Service: litellm-proxy → guardrails:8088"
}

# ── Routes ───────────────────────────────────────────────────────────────────

seed_routes() {
    put_entity "/services/litellm-proxy/routes/v1-inference" '{
        "name":       "v1-inference",
        "paths":      ["/v1"],
        "strip_path": false
    }'
    ok "Route: /v1 → litellm-proxy"

    put_entity "/services/litellm-proxy/routes/health" '{
        "name":       "health",
        "paths":      ["/health"],
        "strip_path": false
    }'
    ok "Route: /health → litellm-proxy"
}

# ── Plugins ──────────────────────────────────────────────────────────────────

seed_plugins() {
    # Correlation ID — attaches X-Request-ID to every request
    put_entity "/plugins/correlation-id" '{
        "name": "correlation-id",
        "config": {
            "header_name": "X-Request-ID",
            "generator":   "uuid"
        }
    }'
    ok "Plugin: correlation-id"

    # Response headers — adds X-Platform and X-API-Version
    put_entity "/plugins/response-transformer" '{
        "name": "response-transformer",
        "config": {
            "add": {
                "headers": [
                    "X-Platform:inference-platform",
                    "X-API-Version:1"
                ]
            }
        }
    }'
    ok "Plugin: response-transformer (X-Platform, X-API-Version)"
}

# ── Main ─────────────────────────────────────────────────────────────────────

main() {
    printf '\nSeeding Kong at %s\n\n' "$KONG_ADMIN"
    wait_for_kong
    seed_services
    seed_routes
    seed_plugins
    printf '\nKong seeding complete.\n'
}

main "$@"
