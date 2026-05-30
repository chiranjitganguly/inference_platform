#!/usr/bin/env bash
# seed-kong.sh — Provision Kong DB-backed configuration via Admin API.
#
# Creates all services, routes, consumers, plugins, and credentials.
# Safe to re-run: idempotent PUT/POST calls skip already-existing entities.
#
# Prerequisites:
#   - Kong is running in DB mode and Admin API is reachable at KONG_ADMIN_URL
#   - SMOKE_API_KEY is set in the environment (from .env)
#
# Usage:
#   make seed-kong
#   KONG_ADMIN_URL=http://localhost:8001 SMOKE_API_KEY=mykey bash scripts/seed-kong.sh
set -euo pipefail

KONG_ADMIN="${KONG_ADMIN_URL:-http://localhost:8001}"

ok()   { printf '[OK]      %s\n' "$*"; }
info() { printf '[INFO]    %s\n' "$*"; }
err()  { printf '[ERROR]   %s\n' "$*" >&2; }
fail() { err "$*"; exit 1; }

# ── Wait for Admin API ────────────────────────────────────────────────────────

wait_for_kong() {
    local attempts=0
    until curl -sf "${KONG_ADMIN}/status" >/dev/null 2>&1; do
        attempts=$(( attempts + 1 ))
        if [[ $attempts -ge 30 ]]; then
            fail "Kong Admin API not reachable at ${KONG_ADMIN} after 30 attempts."
        fi
        info "Waiting for Kong Admin API (${attempts}/30)..."
        sleep 2
    done
    ok "Kong Admin API is reachable."
}

# ── Consumers ─────────────────────────────────────────────────────────────────

create_consumers() {
    info "Creating consumers..."

    if [[ -z "${SMOKE_API_KEY:-}" ]]; then
        fail "SMOKE_API_KEY is not set. Add it to .env and retry."
    fi

    # smoke-test-consumer — the CI/CD validation identity
    curl -sf -X PUT "${KONG_ADMIN}/consumers/smoke-test-consumer" \
        -d "username=smoke-test-consumer" \
        -d "tags[]=smoke-test" \
        >/dev/null
    ok "Consumer: smoke-test-consumer"

    # Provision key credential — suppress duplicate error (idempotent)
    curl -sf -X POST "${KONG_ADMIN}/consumers/smoke-test-consumer/key-auth" \
        -d "key=${SMOKE_API_KEY}" \
        >/dev/null 2>&1 || true
    ok "Key credential provisioned for smoke-test-consumer."
}

# ── Inference service (/v1 and /v2) ──────────────────────────────────────────

create_inference_service() {
    info "Creating litellm-inference service..."

    curl -sf -X PUT "${KONG_ADMIN}/services/litellm-inference" \
        -d "url=http://litellm:4000" \
        -d "connect_timeout=10000" \
        -d "read_timeout=60000" \
        -d "write_timeout=60000" \
        >/dev/null
    ok "Service: litellm-inference"

    curl -sf -X PUT "${KONG_ADMIN}/services/litellm-inference/routes/inference-v1" \
        -d "paths[]=/v1" \
        -d "methods[]=GET" \
        -d "methods[]=POST" \
        -d "strip_path=false" \
        >/dev/null
    ok "Route: /v1 → litellm-inference"

    curl -sf -X PUT "${KONG_ADMIN}/services/litellm-inference/routes/inference-v2" \
        -d "paths[]=/v2" \
        -d "methods[]=GET" \
        -d "methods[]=POST" \
        -d "strip_path=false" \
        >/dev/null
    ok "Route: /v2 → litellm-inference"

    # key-auth at service level — enforces auth on /v1 and /v2
    _ensure_service_plugin litellm-inference key-auth \
        -d "config.key_names[]=Authorization" \
        -d "config.hide_credentials=true" \
        -d "config.key_in_header=true"
    ok "Plugin: key-auth on litellm-inference"
}

# ── Embeddings service (/v1/embeddings, 120 s timeout) ───────────────────────

create_embeddings_service() {
    info "Creating litellm-embeddings service..."

    curl -sf -X PUT "${KONG_ADMIN}/services/litellm-embeddings" \
        -d "url=http://litellm:4000" \
        -d "connect_timeout=10000" \
        -d "read_timeout=120000" \
        -d "write_timeout=120000" \
        >/dev/null
    ok "Service: litellm-embeddings (120 s read timeout)"

    curl -sf -X PUT "${KONG_ADMIN}/services/litellm-embeddings/routes/embeddings" \
        -d "paths[]=/v1/embeddings" \
        -d "methods[]=POST" \
        -d "strip_path=false" \
        >/dev/null
    ok "Route: /v1/embeddings → litellm-embeddings"

    _ensure_service_plugin litellm-embeddings key-auth \
        -d "config.key_names[]=Authorization" \
        -d "config.hide_credentials=true" \
        -d "config.key_in_header=true"
    ok "Plugin: key-auth on litellm-embeddings"
}

# ── Admin services (/v1/spend and /v1/key) ────────────────────────────────────

create_admin_services() {
    info "Creating portal-backend and litellm-admin services..."

    # portal-backend — spend reporting
    curl -sf -X PUT "${KONG_ADMIN}/services/portal-backend" \
        -d "url=http://portal-backend:8092" \
        -d "connect_timeout=10000" \
        -d "read_timeout=15000" \
        -d "write_timeout=15000" \
        >/dev/null
    ok "Service: portal-backend"

    curl -sf -X PUT "${KONG_ADMIN}/services/portal-backend/routes/spend-report" \
        -d "paths[]=/v1/spend" \
        -d "methods[]=GET" \
        -d "strip_path=false" \
        >/dev/null
    ok "Route: /v1/spend → portal-backend"
    # TODO(future): add Kong-level key-auth once admin credential scoping is defined.
    # LiteLLM master key auth uses "Bearer <key>"; Kong key-auth stores raw keys.
    # hide_credentials=true would strip the header before LiteLLM sees it.
    # For now, portal-backend handles its own auth via LITELLM_MASTER_KEY.

    # litellm-admin — virtual key management (LiteLLM enforces master key internally)
    curl -sf -X PUT "${KONG_ADMIN}/services/litellm-admin" \
        -d "url=http://litellm:4000" \
        -d "connect_timeout=10000" \
        -d "read_timeout=30000" \
        -d "write_timeout=30000" \
        >/dev/null
    ok "Service: litellm-admin"

    curl -sf -X PUT "${KONG_ADMIN}/services/litellm-admin/routes/key-management" \
        -d "paths[]=/v1/key" \
        -d "methods[]=GET" \
        -d "methods[]=POST" \
        -d "methods[]=DELETE" \
        -d "strip_path=true" \
        >/dev/null
    ok "Route: /v1/key → litellm-admin"
    # TODO(future): add Kong-level key-auth with admin consumer scoping.
    # LiteLLM validates the master key itself; no Kong auth added here to avoid
    # stripping the Authorization header before LiteLLM's own validation.
}

# ── Health service (/health — no auth) ───────────────────────────────────────

create_health_service() {
    info "Creating litellm-health service (no auth)..."

    curl -sf -X PUT "${KONG_ADMIN}/services/litellm-health" \
        -d "url=http://litellm:4000" \
        -d "connect_timeout=5000" \
        -d "read_timeout=10000" \
        -d "write_timeout=10000" \
        >/dev/null
    ok "Service: litellm-health"

    curl -sf -X PUT "${KONG_ADMIN}/services/litellm-health/routes/health" \
        -d "paths[]=/health" \
        -d "methods[]=GET" \
        -d "strip_path=true" \
        >/dev/null
    ok "Route: /health → litellm-health (no key-auth plugin)"
    # No key-auth plugin on this service — health is unauthenticated by design.
}

# ── Global plugins ────────────────────────────────────────────────────────────

create_global_plugins() {
    info "Installing global plugins..."

    # correlation-id — attach X-Request-ID to every request/response
    if ! _plugin_exists_global correlation-id; then
        curl -sf -X POST "${KONG_ADMIN}/plugins" \
            -d "name=correlation-id" \
            -d "config.header_name=X-Request-ID" \
            -d "config.generator=uuid" \
            -d "config.echo_downstream=true" \
            >/dev/null
    fi
    ok "Global plugin: correlation-id (X-Request-ID)"

    # response-transformer — append platform identity headers to every response
    if ! _plugin_exists_global response-transformer; then
        curl -sf -X POST "${KONG_ADMIN}/plugins" \
            -d "name=response-transformer" \
            -d "config.add.headers[]=X-Platform:inference-platform" \
            -d "config.add.headers[]=X-API-Version:1" \
            >/dev/null
    fi
    ok "Global plugin: response-transformer (X-Platform, X-API-Version)"
}

# ── Verify and print ──────────────────────────────────────────────────────────

verify_setup() {
    info "Verifying setup..."

    local consumer
    consumer=$(curl -sf "${KONG_ADMIN}/consumers/smoke-test-consumer" 2>/dev/null || echo "")
    if [[ -z "$consumer" ]]; then
        fail "smoke-test-consumer not found — seeding may have failed."
    fi
    ok "Consumer: smoke-test-consumer is registered."

    for svc in litellm-inference litellm-embeddings litellm-health portal-backend litellm-admin; do
        local result
        result=$(curl -sf "${KONG_ADMIN}/services/${svc}" 2>/dev/null || echo "")
        if [[ -z "$result" ]]; then
            fail "Service ${svc} not found — seeding incomplete."
        fi
        ok "Service: ${svc} is registered."
    done
}

print_key() {
    printf '\n'
    ok "Smoke-test API key is configured."
    printf '\nTo use it:\n'
    printf '  export SMOKE_API_KEY=%s\n' "${SMOKE_API_KEY}"
    printf '\n  # Authenticated request (expect 200):\n'
    printf '  curl -s http://localhost:8080/v1/models -H "Authorization: %s" | jq .\n' "${SMOKE_API_KEY}"
    printf '\n  # Unauthenticated request (expect 401):\n'
    printf '  curl -s -o /dev/null -w "%%{http_code}" http://localhost:8080/v1/models\n'
    printf '\n  # Health check — no auth needed (expect 200):\n'
    printf '  curl -sf http://localhost:8080/health\n'
    printf '\n'
}

# ── Helpers ───────────────────────────────────────────────────────────────────

# Check whether a global plugin of a given name is already installed.
_plugin_exists_global() {
    local name="$1"
    curl -sf "${KONG_ADMIN}/plugins?name=${name}" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d['data'] else 1)" 2>/dev/null
}

# Install a service-level plugin if not already present on that service.
# Usage: _ensure_service_plugin <service> <plugin> [-d "key=val" ...]
_ensure_service_plugin() {
    local service="$1"
    local plugin="$2"
    shift 2

    local existing
    existing=$(curl -sf "${KONG_ADMIN}/services/${service}/plugins?name=${plugin}" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['data']))" 2>/dev/null || echo "0")

    if [[ "$existing" == "0" ]]; then
        curl -sf -X POST "${KONG_ADMIN}/services/${service}/plugins" \
            -d "name=${plugin}" \
            "$@" \
            >/dev/null
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
    printf '\nSeeding Kong at %s\n\n' "$KONG_ADMIN"
    wait_for_kong
    create_consumers
    create_inference_service
    create_embeddings_service
    create_admin_services
    create_health_service
    create_global_plugins
    verify_setup
    print_key
    printf 'Kong seeding complete.\n'
}

main "$@"
