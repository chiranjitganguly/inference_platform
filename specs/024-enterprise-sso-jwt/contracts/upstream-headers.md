# Contract: Upstream Header Forwarding

**Feature**: `024-enterprise-sso-jwt` | **Version**: 1.0 | **Date**: 2026-06-09

This contract defines the HTTP headers that Kong sets on every authenticated request before forwarding to Guardrails (:8088), LiteLLM (:4000), and OPA (:8181).

---

## Headers Set by Kong (JWT post-function plugin)

| Header | Value Type | Example | Set When |
|---|---|---|---|
| `X-User-Roles` | JSON array string | `["admin","engineer"]` | Always on authenticated requests; empty array `[]` if `roles` claim is `[]` |
| `X-User-Team` | string | `data-science` | Always on authenticated requests |
| `X-User-Sub` | UUID string | `a1b2c3d4-e5f6-7890-abcd-ef1234567890` | Always on authenticated requests |

## Headers Set by Kong (jwt plugin — built-in behaviour)

| Header | Value | Set When |
|---|---|---|
| `X-Consumer-Username` | `keycloak-realm` | JWT validation passes |
| `X-Consumer-ID` | Kong consumer UUID | JWT validation passes |
| `X-Credential-Identifier` | JWT credential ID | JWT validation passes |

## Headers Set by Kong (existing platform behaviour)

| Header | Value | Set When |
|---|---|---|
| `X-Request-ID` | UUID | All requests (correlation ID plugin) |
| `X-Platform` | `inference-platform` | All responses |
| `X-API-Version` | `1` | All responses |
| `W3C-Traceparent` | trace context | All requests (OTel propagation) |

---

## Consumer Usage by Downstream Services

### OPA (:8181)

OPA policy `services/opa/policies/inference.rego` reads:
- `input.request.http.headers["x-user-roles"]` → parse as JSON array → authorise per-model access
- `input.request.http.headers["x-user-team"]` → team-level quota enforcement
- `input.request.http.headers["x-user-sub"]` → principal identifier for audit

### Guardrails (:8088)

Guardrails service reads:
- `X-User-Roles` → pass to OPA sidecar call for ABAC policy decision
- `X-User-Team` → include in audit log entry (`team` field)
- `X-User-Sub` → include in audit log entry (`sub` field)

### LiteLLM (:4000)

LiteLLM reads (after Guardrails forwards):
- `X-User-Team` → spend attribution by team
- `X-User-Sub` → virtual key owner resolution (if BYOK feature active)

---

## Header Forwarding Rules

- Kong strips the raw `Authorization: Bearer <token>` header before forwarding to Guardrails (Kong `jwt` plugin `hide_credentials: true`). Downstream services never see the raw token.
- `X-User-Roles` and `X-User-Team` are set by Kong's `post-function` plugin in the `access` phase, AFTER the `jwt` plugin has validated the token. If the `jwt` plugin rejects the request, the `post-function` does not execute and these headers are never set.
- Downstream services MUST NOT trust `X-User-Roles` or `X-User-Team` headers that originate from outside the Kong gateway. The Kong `request-transformer` plugin should strip these headers from incoming client requests (defence in depth).

---

## Validation Guarantees

By the time Guardrails receives a request:
1. The JWT signature has been verified against the Keycloak realm RS256 public key.
2. The `exp` and `nbf` claims have been validated (30-second clock skew applied).
3. The `iss` and `aud` claims match the configured values.
4. `roles` and `team` claims were present in the original token (Kong returned 403 if missing).
5. `X-User-Roles` and `X-User-Team` headers are populated and trustworthy.

Guardrails and OPA need not re-validate the JWT — they trust the headers set by Kong.
