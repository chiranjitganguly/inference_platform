# Data Model: Enterprise SSO JWT Validation

**Feature**: `024-enterprise-sso-jwt` | **Date**: 2026-06-09

---

## 1. JWT Token Structure

The canonical token issued by Keycloak `inference-platform` realm for the `inference-gateway` client.

### Required Claims

| Claim | Type | Example | Validation Rule |
|---|---|---|---|
| `iss` | string | `http://keycloak:8083/realms/inference-platform` | Must match Kong consumer credential `key` field exactly |
| `sub` | string UUID | `"a1b2c3d4-e5f6-7890-abcd-ef1234567890"` | Must be present; forwarded as `X-User-Sub` |
| `aud` | string or array | `"inference-gateway"` | Must include the configured audience for the route |
| `exp` | Unix timestamp | `1749465600` | Must not be more than 30 s in the past (clock skew: 30 s) |
| `iat` | Unix timestamp | `1749462000` | Must be present (informational) |
| `nbf` | Unix timestamp | `1749462000` | Must not be more than 30 s in the future |
| `roles` | array of strings | `["admin","engineer"]` | MUST be present; MUST be non-null (empty array `[]` is permitted); values are Keycloak realm role names |
| `team` | string | `"data-science"` | MUST be present and non-empty; sourced from Keycloak user attribute `team` via protocol mapper |

### Optional / Standard Claims

| Claim | Type | Notes |
|---|---|---|
| `name` | string | Full display name from Keycloak profile |
| `preferred_username` | string | Keycloak username |
| `email` | string | User's email; not forwarded upstream by Kong |
| `session_state` | string | Keycloak session ID |
| `jti` | string UUID | JWT ID; for future revocation support |
| `azp` | string | Authorised party (client ID that requested the token) |

### Constraints

- Algorithm: RS256 only. HS256 tokens are rejected.
- Maximum token size: 8 KB (Kong default `client_max_body_size` covers this).
- Token lifetime: ≤3600 s (1 hour). Configurable in Keycloak realm settings.
- `roles` MUST reflect Keycloak **realm roles** (not client roles). Client-role mappers must not be used for the `roles` claim to avoid naming collisions.

---

## 2. Keycloak Entities

### Realm: `inference-platform`

| Attribute | Value |
|---|---|
| Realm name | `inference-platform` |
| Access token lifespan | 3600 s (1 hour) |
| Refresh token lifespan | 86400 s (24 hours) |
| SSL required | `external` (none in dev mode) |
| Brute force detection | Enabled |

### Realm Roles

| Role Name | Intended Principals | OPA Policy Usage |
|---|---|---|
| `admin` | Platform operators, SREs | Full model access; cache flush; spend reports |
| `engineer` | Application developers, data scientists | Standard model access; batch inference |
| `viewer` | Read-only observers | Model catalogue read; no inference |

### OIDC Clients

| Client ID | Grant Types | Audience | Purpose |
|---|---|---|---|
| `inference-gateway` | Client Credentials, Authorization Code + PKCE | `inference-gateway` | API token issuance; `serviceAccountsEnabled: true` for service accounts |
| `platform-ui` | Authorization Code + PKCE | `platform-ui` | next-auth v5 browser login session |
| `phoenix-ui` | Bearer-only | `phoenix-ui` | JWT validation for Phoenix UI Kong route |
| `langfuse-ui` | Bearer-only | `langfuse-ui` | JWT validation for Langfuse UI Kong route |

### Protocol Mappers (on `inference-gateway` client)

| Mapper Name | Type | Claim Name | Source |
|---|---|---|---|
| `realm-roles` | User Realm Role | `roles` | Keycloak realm role memberships |
| `team-attribute` | User Attribute | `team` | User attribute key `team` |
| `audience-inference-gateway` | Audience | `aud` | Hardcoded value `inference-gateway` |

> The same role and team mappers are applied to `platform-ui` client so that the next-auth session token also carries `roles` and `team`.

---

## 3. Kong Entities

### Consumer

| Field | Value |
|---|---|
| username | `keycloak-realm` |
| Purpose | Holds the Keycloak realm RS256 public key as a JWT credential |

### JWT Credential (attached to `keycloak-realm` consumer)

| Field | Value |
|---|---|
| `key` | `http://keycloak:8083/realms/inference-platform` (must match `iss` claim) |
| `algorithm` | `RS256` |
| `rsa_public_key` | PEM-formatted Keycloak realm RS256 public key (populated by `seed-kong-jwt.sh`) |

> For Phoenix-ui and Langfuse-ui routes, separate consumers (`phoenix-realm`, `langfuse-realm`) are created with the same `rsa_public_key` but different `key` fields matching the respective `aud` value (Kong jwt plugin can be configured to verify `aud` via `config.key_claim_name: "aud"` on those routes, or via a dedicated Lua check).

### Updated Route/Plugin Structure

| Route Name | Path | Plugin Removed | Plugin Added |
|---|---|---|---|
| `litellm-proxy` | `/v1` | `key-auth` | `jwt` + `post-function` |
| `models-catalogue` | `/v1/models` | `key-auth` | `jwt` |
| `litellm-embeddings-route` | `/v1/embeddings` | `key-auth` | `jwt` + `post-function` |
| `cache-flush` | `/cache/flush` | `key-auth` | `jwt` (admin role required — enforced by OPA, not Kong) |
| `phoenix-ui-route` | `/phoenix` | *(new)* | `jwt` (audience: `phoenix-ui`) |
| `langfuse-ui-route` | `/langfuse` | *(new)* | `jwt` (audience: `langfuse-ui`) |

### Kong JWT Plugin Configuration (all `/v1/*` routes)

```yaml
- name: jwt
  config:
    key_claim_name: iss          # match consumer credential by iss claim value
    claims_to_verify:
      - exp
      - nbf
    clock_skew: 30               # 30-second grace on exp and nbf (FR-003, FR-005)
    anonymous: ~                 # no anonymous passthrough
    header_names:
      - Authorization
    uri_param_names: []
    cookie_names: []
    run_on_preflight: true
    maximum_expiration: 0        # no additional max lifetime cap beyond token's exp
```

---

## 4. Upstream Header Contract

Headers set by the Kong `post-function` Lua plugin on all authenticated requests forwarded to Guardrails → LiteLLM:

| Header | Type | Example | Source |
|---|---|---|---|
| `X-User-Roles` | JSON string (array) | `["admin","engineer"]` | JWT `roles` claim |
| `X-User-Team` | string | `data-science` | JWT `team` claim |
| `X-User-Sub` | string (UUID) | `a1b2c3d4-...` | JWT `sub` claim |

> `X-Consumer-Username` is also set by Kong's `jwt` plugin itself (value: `keycloak-realm`). This existing header is not used for authorisation downstream.

---

## 5. Audit Log Entry Schema

Every authentication decision written to Loki (FR-012):

```json
{
  "timestamp": "2026-06-09T12:34:56.789Z",
  "event_type": "auth_accepted | auth_rejected",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "reason_code": "ok | token_missing | token_expired | token_tampered | token_invalid_iss | token_invalid_aud | token_missing_roles | token_missing_team | jwks_unavailable",
  "sub": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "roles": ["admin", "engineer"],
  "team": "data-science",
  "model": "gpt-4o"
}
```

**Excluded from all log entries**: raw JWT string, prompt text, response text, `email`, `preferred_username`.

---

## 6. State Transitions

### Token Lifecycle (from gateway perspective)

```
[Request arrives]
       │
       ▼
[Authorization header present?]
   NO → 401 auth_missing
   YES ▼
[JWT structurally valid (3 segments)?]
   NO → 401 token_tampered
   YES ▼
[iss claim matches known consumer credential?]
   NO → 401 token_invalid_iss
   YES ▼
[RS256 signature valid against stored public key?]
   NO → 401 token_tampered
   YES ▼
[exp claim ≤ now + 30s?]
   NO → 401 token_expired
   YES ▼
[nbf claim ≥ now - 30s?]
   NO → 401 token_not_yet_valid
   YES ▼
[aud claim includes route audience?]
   NO → 401 token_invalid_aud
   YES ▼
[roles claim present and non-null?]
   NO → 403 token_missing_roles
   YES ▼
[team claim present and non-empty?]
   NO → 403 token_missing_team
   YES ▼
[Set X-User-Roles, X-User-Team, X-User-Sub headers]
       │
       ▼
[Forward to Guardrails :8088]
```
