#!/usr/bin/env bash
# seed-kong-jwt.sh — Fetch Keycloak realm RS256 public key and register it as
# Kong JWT consumer credentials. Idempotent: safe to re-run after key rotation.
#
# Usage:
#   make seed-kong-jwt                          # uses defaults
#   KEYCLOAK_URL=http://keycloak:8083 make seed-kong-jwt
#
# Prerequisites:
#   - Keycloak running and healthy on KEYCLOAK_URL
#   - Kong Admin API reachable on KONG_ADMIN_URL
#   - KEYCLOAK_REALM set (default: inference-platform)
set -euo pipefail

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8083}"
KONG_ADMIN_URL="${KONG_ADMIN_URL:-http://localhost:8001}"
KEYCLOAK_REALM="${KEYCLOAK_REALM:-inference-platform}"
ISSUER="${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}"
TIMEOUT=5

ok()   { printf '[OK]   %s\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

# ── Wait for Keycloak ─────────────────────────────────────────────────────────
info "Waiting for Keycloak at ${KEYCLOAK_URL}/health/ready ..."
for i in $(seq 1 30); do
  if curl -sf --max-time "${TIMEOUT}" "${KEYCLOAK_URL}/health/ready" >/dev/null 2>&1; then
    ok "Keycloak is ready"
    break
  fi
  if [[ "$i" -eq 30 ]]; then
    fail "Keycloak did not become ready after 150 s — is 'make up-auth' running?"
  fi
  sleep 5
done

# ── Fetch realm public key ────────────────────────────────────────────────────
info "Fetching RS256 public key from realm '${KEYCLOAK_REALM}' ..."
REALM_JSON=$(curl -sf --max-time "${TIMEOUT}" "${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}") \
  || fail "Cannot reach ${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM} — is the realm imported?"

PUBLIC_KEY=$(echo "${REALM_JSON}" | python3 -c "import sys,json; print(json.load(sys.stdin)['public_key'])") \
  || fail "Could not extract 'public_key' from realm metadata"

# Wrap base64-DER key in PEM headers (fold at 64 chars per line)
PEM="-----BEGIN PUBLIC KEY-----
$(echo "${PUBLIC_KEY}" | fold -w 64)
-----END PUBLIC KEY-----"

ok "Public key extracted (${#PUBLIC_KEY} base64 chars)"

# ── Helper: upsert a Kong consumer ───────────────────────────────────────────
upsert_consumer() {
  local username="$1"
  curl -sf --max-time "${TIMEOUT}" -X PUT \
    "${KONG_ADMIN_URL}/consumers/${username}" \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"${username}\"}" >/dev/null \
    && ok "Consumer '${username}' upserted"
}

# ── Helper: upsert a JWT credential ──────────────────────────────────────────
upsert_jwt_credential() {
  local consumer="$1"
  local key="$2"     # value that must match the claim named by key_claim_name
  local pem="$3"

  # Check if a credential with this key already exists
  existing_id=$(curl -sf --max-time "${TIMEOUT}" \
    "${KONG_ADMIN_URL}/consumers/${consumer}/jwt" \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
for c in data.get('data', []):
    if c.get('key') == sys.argv[1]:
        print(c['id'])
        break
" "${key}" 2>/dev/null || echo "")

  escaped_pem=$(echo "${pem}" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")

  if [[ -n "${existing_id}" ]]; then
    # Update existing credential
    curl -sf --max-time "${TIMEOUT}" -X PATCH \
      "${KONG_ADMIN_URL}/consumers/${consumer}/jwt/${existing_id}" \
      -H "Content-Type: application/json" \
      -d "{\"key\": \"${key}\", \"algorithm\": \"RS256\", \"rsa_public_key\": ${escaped_pem}}" >/dev/null \
      && ok "JWT credential for consumer '${consumer}' (key='${key}') updated"
  else
    # Create new credential
    curl -sf --max-time "${TIMEOUT}" -X POST \
      "${KONG_ADMIN_URL}/consumers/${consumer}/jwt" \
      -H "Content-Type: application/json" \
      -d "{\"key\": \"${key}\", \"algorithm\": \"RS256\", \"rsa_public_key\": ${escaped_pem}}" >/dev/null \
      && ok "JWT credential for consumer '${consumer}' (key='${key}') created"
  fi
}

# ── Upsert consumers and credentials ─────────────────────────────────────────

# API routes: iss claim = realm issuer URL
upsert_consumer "keycloak-realm"
upsert_jwt_credential "keycloak-realm" "${ISSUER}" "${PEM}"

# Phoenix UI route: aud claim = "phoenix-ui"
upsert_consumer "phoenix-realm"
upsert_jwt_credential "phoenix-realm" "phoenix-ui" "${PEM}"

# Langfuse UI route: aud claim = "langfuse-ui"
upsert_consumer "langfuse-realm"
upsert_jwt_credential "langfuse-realm" "langfuse-ui" "${PEM}"

ok "All Kong JWT credentials seeded successfully"
printf '\nTo verify:\n'
printf '  curl -s http://localhost:8001/consumers/keycloak-realm/jwt | python3 -m json.tool\n\n'
