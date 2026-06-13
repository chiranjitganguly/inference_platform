# Research: Enterprise SSO JWT Validation

**Feature**: `024-enterprise-sso-jwt` | **Date**: 2026-06-09

---

## Decision 1: Kong JWT Validation Plugin — Built-in `jwt` vs `openid-connect`

**Decision**: Use Kong 3.6 built-in `jwt` plugin.

**Rationale**: Kong OSS 3.6 ships the `jwt` plugin natively. It performs RS256 signature verification using a stored PEM public key, validates `exp`/`nbf` with configurable `clock_skew`, and matches tokens by the `iss` claim. This is sufficient for the platform's requirements (FR-001–FR-005, FR-010–FR-011). The `openid-connect` plugin that can fetch JWKS dynamically per-request is Kong Enterprise only and introduces a paid dependency.

**Alternatives considered**:
- `openid-connect` (Kong Enterprise): Dynamic JWKS fetch, automatic key rotation support, browser redirect for UI flows. Rejected — requires Kong Enterprise licence; not compatible with Kong OSS 3.6.
- `lua-resty-openidc` (third-party Lua module): Provides dynamic OIDC. Rejected — requires custom Kong image build and is not pinned to a stable version in the project.
- Validate JWT inside Guardrails service: Would bypass the edge-gate principle (Constitution §I). Rejected.

**Key implication**: Key rotation is not automatic. When Keycloak rotates its realm signing key, operators must re-run `make seed-kong-jwt` to refresh the Kong jwt credential. The 5-minute recovery SLO (SC-004) is met by the operational runbook, not by automated polling.

---

## Decision 2: Keycloak Realm Public Key Extraction Method

**Decision**: Fetch the realm's current RS256 public key from `GET {keycloak}/realms/{realm}` → `$.public_key` field (base64-encoded DER), then wrap in PEM headers.

**Rationale**: Keycloak 24.0.3 exposes the active realm signing key at its realm metadata endpoint as `public_key` (base64-DER). This is simpler than parsing the JWKS JWK Set (`n`/`e` RSA components), requires only Python stdlib (no `cryptography` package), and is stable across Keycloak 20–24.

```bash
PUBLIC_KEY=$(curl -sf http://keycloak:8083/realms/inference-platform \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['public_key'])")
# Wrap in PEM format (fold at 64 chars)
PEM="-----BEGIN PUBLIC KEY-----\n$(echo "$PUBLIC_KEY" | fold -w 64)\n-----END PUBLIC KEY-----\n"
```

**Alternatives considered**:
- Parse JWKS endpoint (`/realms/{realm}/protocol/openid-connect/certs`): Would require converting `n`/`e` RSA integer components to PEM, needs `cryptography` Python package. Rejected — unnecessary complexity.
- Use `openssl` JWK-to-PEM: No built-in JWK support in openssl CLI. Rejected.

**Kong consumer credential**:
- Consumer username: `keycloak-realm`
- JWT credential `key` field: the issuer URL (`http://keycloak:8083/realms/inference-platform`) — must match `iss` claim in tokens
- JWT credential `algorithm`: `RS256`
- JWT credential `rsa_public_key`: PEM string from above

---

## Decision 3: JWT Claim Forwarding to Downstream Services

**Decision**: Kong `post-function` Lua plugin, running in the `access` phase after the `jwt` plugin (priority -1 vs jwt priority 1005), decodes the JWT payload from the `Authorization` header and sets upstream request headers.

**Headers set**:
- `X-User-Roles`: JSON array string of realm role names, e.g. `["admin","engineer"]`
- `X-User-Team`: team string, e.g. `"data-science"`
- `X-User-Sub`: subject UUID, e.g. `"a1b2c3d4-..."`

**Rationale**: The `jwt` plugin validates the token and sets `X-Consumer-Username` from the matched Kong consumer, but does not extract arbitrary JWT claims. A Lua `post-function` in the `access` phase runs after jwt validation is complete (guaranteed by plugin priority ordering), so it can safely decode the payload without re-verifying the signature. The payload is base64url-decoded from the middle segment of the JWT — no cryptography needed here since trust is already established.

**Alternatives considered**:
- Kong `request-transformer` plugin: Static transformations only; cannot read JWT payload dynamically. Rejected.
- Forward raw `Authorization` header and have each downstream service parse JWT: Defeats the purpose of centralised extraction; OPA would need JWT-parsing logic. Rejected.
- Custom Kong plugin (Go/Lua packaged): Over-engineered for a simple claim extraction. Rejected.

**Safety note**: The Lua function only runs if the `jwt` plugin has already set `ngx.ctx.authenticated_consumer` (meaning validation passed). This is guaranteed by the Kong plugin pipeline ordering — a failed `jwt` plugin short-circuits and the `post-function` never executes for rejected requests.

---

## Decision 4: Phoenix and Langfuse UI Protection Mechanism

**Decision**: Add Kong routes for `/phoenix` and `/langfuse` with the `jwt` plugin using separate Keycloak clients (`phoenix-ui`, `langfuse-ui`) with distinct `aud` (audience) values. Kong performs JWT validation; browser redirect to Keycloak login is handled by the platform-ui client-side logic (next-auth session check).

**Rationale**: Kong OSS `jwt` plugin is the consistent mechanism already used for API routes. Adding Kong proxy routes for Phoenix (port 6006) and Langfuse (port 3002) means all external access goes through the single Kong gateway (port 8080), consistent with Constitution Principle I. Token audience isolation (FR-017) is enforced by configuring separate `config.anonymous` and consumer credential keys per route.

**Practical browser-flow detail**: The Kong `jwt` plugin returns `401 Unauthorized` for unauthenticated browser requests (not a 302 redirect). The platform-ui session middleware detects the 401 and redirects the browser to the Keycloak login page. After login, the user's browser receives a session cookie (managed by next-auth), and subsequent UI requests include the JWT extracted from the session. This satisfies the spec acceptance scenario (US5) because the user IS redirected to Keycloak — the redirect is triggered client-side by the platform-ui, which is the appropriate layer for browser UX.

**Audience values**:
- API routes: `inference-gateway` (client ID doubles as audience)
- Phoenix UI route: `phoenix-ui`
- Langfuse UI route: `langfuse-ui`

**Alternatives considered**:
- OAuth2 Proxy sidecar per service: Would protect Phoenix/Langfuse independently of Kong; adds two more containers. Rejected — increases memory footprint on the 8 GB dev Mac.
- Keycloak Gatekeeper: Deprecated (replaced by OAuth2 Proxy). Rejected.
- Network-level isolation (no external port): Phoenix/Langfuse ports already not exposed outside Docker network (only Kong:8080 is public). The Kong reverse-proxy routes are the public-facing access path. This decision is already implemented by the platform architecture.

---

## Decision 5: next-auth v5 Keycloak Provider Configuration

**Decision**: Configure next-auth v5 in `services/platform-ui/` with the Keycloak built-in provider. Access token stored in session JWT; `roles` and `team` claims extracted and stored in the next-auth session for UI role-based rendering.

**Key configuration points**:
- Provider: `import Keycloak from "next-auth/providers/keycloak"`
- Issuer: `http://keycloak:8083/realms/inference-platform` (internal Docker DNS)
- Client ID: `platform-ui`
- Client Secret: `${PLATFORM_UI_CLIENT_SECRET}`
- Scope: `openid profile email roles`
- Access token forwarding: next-auth `jwt` callback extracts `access_token` from provider tokens for forwarding to Kong in API calls
- The `roles` and `team` claims are mapped into the next-auth `session.user` object via the `session` callback

**Middleware**: `services/platform-ui/middleware.ts` uses `auth()` from next-auth v5 to protect all routes except `/api/auth/*` and `/health`.

**Alternatives considered**:
- Manual OAuth2 PKCE implementation: Unnecessary given next-auth v5 supports Keycloak natively. Rejected.
- next-auth v4: Project stack specifies next-auth v5. Rejected — would conflict with locked versions.

---

## Decision 6: Keycloak Service Docker Compose Profile

**Decision**: Keycloak service added under `profiles: [auth, all]`. Realm auto-imported using Keycloak's `--import-realm` flag pointing to `/opt/keycloak/data/import/realm-export.json`.

**Startup sequence**: `make up-auth` starts Keycloak (waits for postgres healthcheck). After Keycloak is healthy, operators run `make seed-kong-jwt` to push the realm public key into Kong.

**`make seed-kong-jwt`** added to Makefile as: `bash scripts/seed-kong-jwt.sh`

**Realm import idempotency**: Keycloak 24's `--import-realm` skips import if the realm already exists. To force re-import (e.g. after realm-export.json changes), use `--override realm`.

---

## Resolved Unknowns Summary

| Unknown | Resolution |
|---|---|
| Key extraction from Keycloak | Realm metadata endpoint `$.public_key` (base64-DER) → PEM wrap |
| JWT claim forwarding in Kong | Lua `post-function` in access phase; headers `X-User-Roles`, `X-User-Team`, `X-User-Sub` |
| Phoenix/Langfuse browser redirect | Client-side (platform-ui middleware); Kong jwt returns 401, next-auth redirects to Keycloak |
| Clock skew enforcement | `config.clock_skew: 30` in Kong `jwt` plugin config |
| Key rotation recovery | Manual: `make seed-kong-jwt`; SLO: ≤5 min (operators informed via runbook) |
| Audience isolation | Separate Keycloak clients (`inference-gateway`, `platform-ui`, `phoenix-ui`, `langfuse-ui`) with distinct `aud` values |
