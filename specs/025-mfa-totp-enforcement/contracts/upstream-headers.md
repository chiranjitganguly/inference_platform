# Contract: Upstream Headers After MFA Authentication

**Branch**: `025-mfa-totp-enforcement` | **Date**: 2026-06-13

This document defines the JWT claims and Kong-forwarded headers that downstream services (Guardrails, LiteLLM, OPA) receive after a user completes MFA authentication. No changes to this contract are required from feature 024 — MFA enforcement is transparent to the downstream chain.

---

## JWT Claims (unchanged from feature 024)

Keycloak issues the JWT only after the full browser-mfa flow completes (password + OTP). The token structure is identical to feature 024; no new claims are added for MFA status.

```json
{
  "exp": 1749820000,
  "iat": 1749816400,
  "jti": "<uuid>",
  "iss": "http://keycloak:8083/realms/inference-platform",
  "aud": ["platform-ui", "account"],
  "sub": "<user-uuid>",
  "typ": "Bearer",
  "azp": "platform-ui",
  "realm_access": {
    "roles": ["operator"]
  },
  "preferred_username": "alice",
  "email": "alice@example.com"
}
```

**MFA enforcement guarantee**: The presence of a valid, unexpired Keycloak JWT signed by the `inference-platform` realm implicitly guarantees that the holder completed MFA (if required for their account). No separate `amr` or `acr` claim is required for v1 enforcement.

---

## Kong-Forwarded Headers (unchanged from feature 024)

Kong validates the JWT and forwards these headers to Guardrails:

| Header | Value | Source |
|--------|-------|--------|
| `Authorization` | `Bearer <jwt>` | next-auth session → Kong passthrough |
| `X-Consumer-Username` | `<preferred_username>` | Kong JWT plugin consumer mapping |
| `X-Credential-Identifier` | `<jti>` | Kong JWT plugin |
| `X-Request-ID` | `<uuid>` | Kong correlation ID plugin |
| `X-Forwarded-For` | `<client-ip>` | Kong |

No new headers are added by MFA enforcement. The downstream OPA policy, LiteLLM BYOK mapping, and audit logging all continue to use the same header contract as feature 024.

---

## Invariants

- A request reaching Kong with a valid JWT MUST have been issued by Keycloak after completing all required authentication steps for that user (including MFA if configured).
- Kong MUST NOT forward requests with an invalid, expired, or missing JWT to Guardrails. This is enforced by the existing `jwt` plugin configuration from feature 024.
- Adding or removing MFA requirement for a user does NOT require changes to Kong configuration, OPA policy, or LiteLLM routing rules.
