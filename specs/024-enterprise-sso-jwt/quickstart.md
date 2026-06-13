# Quickstart: Enterprise SSO JWT Validation

**Feature**: `024-enterprise-sso-jwt` | **Date**: 2026-06-09

Get this feature running locally in under 10 minutes.

---

## Prerequisites

- `make up-core` is running and healthy (`make ps` shows all core services green)
- `.env` contains all required secrets (copy from `.env.example` and fill values)
- Keycloak PostgreSQL database exists (`keycloak` database in shared PostgreSQL — created by `scripts/init-db.sql`)

---

## Step 1: Add required environment variables

Add these to your `.env` file (values are examples — use real secrets):

```bash
# Keycloak admin credentials
KEYCLOAK_ADMIN=admin
KEYCLOAK_ADMIN_PASSWORD=change-me-in-production

# Keycloak realm name (must match realm-export.json)
KEYCLOAK_REALM=inference-platform

# OIDC client secrets (generate with: openssl rand -base64 32)
INFERENCE_GATEWAY_CLIENT_SECRET=your-inference-gateway-secret
PLATFORM_UI_CLIENT_SECRET=your-platform-ui-secret
PHOENIX_UI_CLIENT_SECRET=your-phoenix-ui-secret
LANGFUSE_UI_CLIENT_SECRET=your-langfuse-ui-secret

# next-auth (platform-ui)
NEXTAUTH_SECRET=your-nextauth-secret-32-chars-min
NEXTAUTH_URL=http://localhost:3001
```

---

## Step 2: Start Keycloak

```bash
make up-auth
# Waits for postgres, then starts Keycloak on :8083
# Realm 'inference-platform' is auto-imported from services/keycloak/realm-export.json

# Confirm Keycloak is ready:
curl -sf http://localhost:8083/realms/inference-platform | python3 -c "import sys,json; d=json.load(sys.stdin); print('Realm ready:', d['realm'])"
# Expected: Realm ready: inference-platform
```

---

## Step 3: Seed Kong JWT credential

```bash
make seed-kong-jwt
# Fetches Keycloak realm RS256 public key → creates Kong consumers + JWT credentials
# Idempotent: safe to re-run after key rotation

# Confirm Kong consumer exists:
curl -sf http://localhost:8001/consumers/keycloak-realm/jwt | python3 -m json.tool | grep '"key"'
# Expected: "key": "http://keycloak:8083/realms/inference-platform",
```

---

## Step 4: Obtain a JWT from Keycloak

```bash
# Service account token (client credentials grant)
TOKEN=$(curl -s -X POST \
  http://localhost:8083/realms/inference-platform/protocol/openid-connect/token \
  -d "grant_type=client_credentials" \
  -d "client_id=inference-gateway" \
  -d "client_secret=${INFERENCE_GATEWAY_CLIENT_SECRET}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "Token obtained: ${TOKEN:0:50}..."
```

---

## Step 5: Smoke test — valid token accepted

```bash
# Should return 200 OK with model list
curl -sf http://localhost:8080/v1/models \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | head -10
```

---

## Step 6: Smoke test — expired/tampered token rejected

```bash
# Missing token → 401
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/v1/models
# Expected: 401

# Tampered token (flip one character in payload) → 401
TAMPERED=$(echo "$TOKEN" | sed 's/\(eyJ[^.]*\.\)[^.]*\(.*\)/\1AAAA\2/')
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/v1/models \
  -H "Authorization: Bearer $TAMPERED"
# Expected: 401
```

---

## Step 7: Verify claim forwarding

```bash
# The guardrails service should receive X-User-Roles and X-User-Team headers.
# Check guardrails logs for the forwarded headers:
make logs svc=guardrails | grep "X-User-"
```

---

## Step 8: Run full smoke test

```bash
make smoke
# All checks should pass including JWT-authenticated endpoints
```

---

## Key Rotation (operational runbook)

When Keycloak rotates its realm signing key (or you manually rotate it):

```bash
# 1. Re-run the seed script — fetches the new public key and updates Kong
make seed-kong-jwt

# 2. Verify new key is active
curl -sf http://localhost:8001/consumers/keycloak-realm/jwt | python3 -m json.tool | grep '"key"'

# 3. Confirm new tokens are accepted
TOKEN=$(...)  # obtain fresh token
curl -sf http://localhost:8080/v1/models -H "Authorization: Bearer $TOKEN"
# Expected: 200 OK
```

Target recovery time: ≤5 minutes (SC-004).

---

## Accessing Phoenix and Langfuse UIs

After seeding, Phoenix and Langfuse are accessible through Kong with JWT auth:

```bash
# Obtain a phoenix-ui scoped token (requires phoenix-ui client config in Keycloak)
PHOENIX_TOKEN=$(curl -s -X POST \
  http://localhost:8083/realms/inference-platform/protocol/openid-connect/token \
  -d "grant_type=client_credentials" \
  -d "client_id=phoenix-ui" \
  -d "client_secret=${PHOENIX_UI_CLIENT_SECRET}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -sf http://localhost:8080/phoenix -H "Authorization: Bearer $PHOENIX_TOKEN"
# Expected: Phoenix UI HTML response
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `401 Unauthorized` with valid token | Kong jwt credential not seeded | Run `make seed-kong-jwt` |
| `401` — `No credentials found for given 'iss'` | Token `iss` doesn't match Kong consumer key | Confirm Keycloak realm URL matches `KEYCLOAK_REALM` in .env |
| `403` — `token_missing_roles` | Keycloak realm role mapper not configured | Check realm-export.json includes `realm-roles` protocol mapper |
| `403` — `token_missing_team` | User has no `team` attribute in Keycloak | Set `team` user attribute in Keycloak admin console |
| Keycloak not starting | `keycloak` database missing | Run `psql -U postgres -f scripts/init-db.sql` then `make up-auth` |
| `make up-auth` fails — Keycloak unhealthy | Postgres not ready | Check `make ps` — postgres must be healthy first |
