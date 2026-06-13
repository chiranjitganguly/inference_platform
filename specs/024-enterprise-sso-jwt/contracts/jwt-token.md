# Contract: JWT Token

**Feature**: `024-enterprise-sso-jwt` | **Version**: 1.0 | **Date**: 2026-06-09

This contract defines the JWT token structure that the Kong gateway accepts on all `/v1/*` routes. Tokens are issued by the Keycloak `inference-platform` realm.

---

## Token Format

```
Authorization: Bearer <base64url(header)>.<base64url(payload)>.<base64url(signature)>
```

### Header

```json
{
  "alg": "RS256",
  "typ": "JWT",
  "kid": "<keycloak-key-id>"
}
```

- `alg` MUST be `RS256`. Tokens with `alg: HS256` or `alg: none` are rejected.

### Payload (minimum required claims)

```json
{
  "iss": "http://keycloak:8083/realms/inference-platform",
  "sub": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "aud": "inference-gateway",
  "exp": 1749465600,
  "iat": 1749462000,
  "nbf": 1749462000,
  "roles": ["admin"],
  "team": "platform-engineering"
}
```

---

## Validation Rules

| Claim | Rule | Error on Failure |
|---|---|---|
| `iss` | Must equal `http://keycloak:8083/realms/inference-platform` | `401 token_invalid_iss` |
| `aud` | Must include the target route's audience value | `401 token_invalid_aud` |
| `exp` | Must be ≥ `now - 30s` | `401 token_expired` |
| `nbf` | Must be ≤ `now + 30s` | `401 token_not_yet_valid` |
| Signature | Must verify against stored RS256 public key | `401 token_tampered` |
| `roles` | Must be present and non-null (empty array permitted) | `403 token_missing_roles` |
| `team` | Must be present and non-empty string | `403 token_missing_team` |

---

## Audience Values by Route

| Kong Route | Required `aud` Value | Keycloak Client |
|---|---|---|
| `/v1/*` (API) | `inference-gateway` | `inference-gateway` |
| `/phoenix/*` (UI) | `phoenix-ui` | `phoenix-ui` |
| `/langfuse/*` (UI) | `langfuse-ui` | `langfuse-ui` |
| `/v1/*` (platform-ui sessions) | `platform-ui` | `platform-ui` |

---

## Token Acquisition

### Developer / Service Account (Client Credentials)

```bash
curl -s -X POST \
  http://localhost:8083/realms/inference-platform/protocol/openid-connect/token \
  -d "grant_type=client_credentials" \
  -d "client_id=inference-gateway" \
  -d "client_secret=${INFERENCE_GATEWAY_CLIENT_SECRET}"
```

Response: `{ "access_token": "...", "expires_in": 3600, "token_type": "Bearer" }`

### Interactive User (Authorization Code + PKCE)

Initiate: `GET http://localhost:8083/realms/inference-platform/protocol/openid-connect/auth?client_id=inference-gateway&response_type=code&scope=openid%20roles&redirect_uri=...`

---

## Error Responses

All validation failures return structured JSON per Constitution §4.4:

```json
{
  "error": "Unauthorized",
  "message": "JWT token expired",
  "detail": { "reason": "token_expired" }
}
```

HTTP status: `401` for invalid/missing/expired/tampered tokens; `403` for missing required claims.
