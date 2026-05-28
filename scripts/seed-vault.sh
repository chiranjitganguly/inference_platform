#!/usr/bin/env bash
# Seed Vault with all platform secrets.
# Idempotent — vault kv put overwrites existing values at the same path.
# Requires: VAULT_ADDR and VAULT_TOKEN set in environment (loaded from .env via make).
set -euo pipefail

VAULT_ADDR="${VAULT_ADDR:-http://localhost:8200}"
VAULT_TOKEN="${VAULT_TOKEN:-root}"

ok()   { printf '[OK]      %s\n' "$*"; }
info() { printf '[INFO]    %s\n' "$*"; }
err()  { printf '[ERROR]   %s\n' "$*" >&2; }

export VAULT_ADDR VAULT_TOKEN

wait_for_vault() {
    local attempts=0
    until vault status >/dev/null 2>&1; do
        attempts=$(( attempts + 1 ))
        if [[ $attempts -ge 30 ]]; then
            err "Vault not reachable at ${VAULT_ADDR} after 30 attempts."
            exit 1
        fi
        printf '[INFO]    Waiting for Vault (%d/30)...\n' "$attempts"
        sleep 2
    done

    local sealed
    sealed=$(vault status -format=json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['sealed'])" 2>/dev/null || echo "true")
    if [[ "$sealed" == "true" ]]; then
        err "Vault is sealed. Unseal it before running seed-vault."
        exit 1
    fi
    ok "Vault is reachable and unsealed."
}

enable_kv() {
    # Enable KV v2 at 'secret/' if not already enabled — idempotent.
    if ! vault secrets list -format=json 2>/dev/null | python3 -c "import sys,json; sys.exit(0 if 'secret/' in json.load(sys.stdin) else 1)" 2>/dev/null; then
        vault secrets enable -path=secret kv-v2 >/dev/null
        ok "KV v2 engine enabled at secret/"
    else
        info "KV v2 engine already enabled at secret/"
    fi
}

# Write a secret — vault kv put is idempotent (overwrites).
# Usage: put_secret <path> <key=placeholder> ...
put_secret() {
    local path="$1"; shift
    vault kv put "secret/${path}" "$@" >/dev/null || {
        err "Failed to write secret at secret/${path}"
        exit 1
    }
    ok "Secret: secret/${path}"
}

# ── Secrets ───────────────────────────────────────────────────────────────────

seed_secrets() {
    # LiteLLM
    put_secret "litellm" \
        "master_key=${LITELLM_MASTER_KEY:-PLACEHOLDER}" \
        "salt_key=${LITELLM_SALT_KEY:-PLACEHOLDER}"

    # PostgreSQL
    put_secret "postgres" \
        "password=${POSTGRES_PASSWORD:-PLACEHOLDER}"

    # Redis
    put_secret "redis" \
        "password=${REDIS_PASSWORD:-PLACEHOLDER}"

    # LLM providers
    put_secret "llm/openai" \
        "api_key=${OPENAI_API_KEY:-PLACEHOLDER}"

    put_secret "llm/anthropic" \
        "api_key=${ANTHROPIC_API_KEY:-PLACEHOLDER}"

    put_secret "llm/google" \
        "api_key=${GOOGLE_API_KEY:-PLACEHOLDER}"

    put_secret "llm/cohere" \
        "api_key=${COHERE_API_KEY:-PLACEHOLDER}"

    # Langfuse
    put_secret "langfuse" \
        "public_key=${LANGFUSE_PUBLIC_KEY:-PLACEHOLDER}" \
        "secret_key=${LANGFUSE_SECRET_KEY:-PLACEHOLDER}"

    # Keycloak
    put_secret "keycloak" \
        "admin_password=${KEYCLOAK_ADMIN_PASSWORD:-PLACEHOLDER}" \
        "client_secret=${KEYCLOAK_CLIENT_SECRET:-PLACEHOLDER}"

    # Grafana
    put_secret "grafana" \
        "admin_password=${GRAFANA_ADMIN_PASSWORD:-PLACEHOLDER}"
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
    printf '\nSeeding Vault at %s\n\n' "$VAULT_ADDR"
    wait_for_vault
    enable_kv
    seed_secrets
    printf '\nVault seeding complete.\n'
}

main "$@"
