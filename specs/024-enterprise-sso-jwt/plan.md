# Implementation Plan: Enterprise SSO JWT Validation

**Branch**: `024-enterprise-sso-jwt` | **Date**: 2026-06-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/024-enterprise-sso-jwt/spec.md`

---

## Summary

Replace Kong's static `key-auth` plugin on all `/v1/*` routes with the built-in Kong `jwt` plugin backed by Keycloak 24.0.3 (`inference-platform` realm). Keycloak is added to docker-compose under the `auth` profile and imports the realm from `services/keycloak/realm-export.json` at startup. A seed script fetches the realm RS256 public key at boot and registers it as a Kong JWT consumer credential. A Lua post-function plugin extracts `roles` and `team` JWT claims and forwards them as `X-User-Roles` / `X-User-Team` headers to downstream services (Guardrails, LiteLLM, OPA). Phoenix UI and Langfuse UI are fronted by new Kong routes with JWT validation using separate Keycloak clients and audience values. Platform UI uses next-auth v5 with Keycloak as its OIDC provider.

---

## Technical Context

**Language/Version**: YAML (Kong declarative config, docker-compose), JSON (Keycloak realm export), Lua (Kong post-function), TypeScript 5.5 strict (platform-ui next-auth), Bash (seed scripts)

**Primary Dependencies**:
- Kong 3.6 (built-in `jwt` plugin, `post-function` plugin)
- Keycloak 24.0.3 (`quay.io/keycloak/keycloak:24.0.3`)
- next-auth v5 (`@auth/core`, `next-auth` with Keycloak provider)
- Python 3.x (key conversion in seed script — stdlib only)

**Storage**: Kong PostgreSQL (`kong` DB — consumers, jwt credentials); Keycloak PostgreSQL (`keycloak` DB — realm, clients, users)

**Testing**: `scripts/smoke-test.sh` (curl-based, JWT token obtained from Keycloak); `make smoke`

**Target Platform**: Docker Compose on Mac (OrbStack); `auth` profile

**Project Type**: Platform configuration (Kong declarative config, docker-compose service, Keycloak realm, minimal TypeScript auth config)

**Performance Goals**: JWT validation ≤5 ms p99 (cryptographic verify in Kong memory; Keycloak not contacted per-request)

**Constraints**:
- Clock skew grace: 30 seconds on `exp` and `nbf`
- JWKS cache (Kong consumer credential) refreshed at seed time and on key-rotation event; TTL: refreshed by re-running `make seed-kong-jwt`
- Fail-closed: JWKS credential missing → Kong returns 401 (consumer credential lookup fails)
- 30-second clock skew: `config.clock_skew: 30` in Kong jwt plugin

**Scale/Scope**: Single Keycloak realm, four OIDC clients (inference-gateway, platform-ui, phoenix-ui, langfuse-ui), three realm roles (admin, engineer, viewer), one user attribute (team)

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Evidence |
|---|---|---|
| I — Request Flow Integrity | ✅ PASS | JWT validation added **at Kong** (edge); Kong→Guardrails→LiteLLM chain is preserved and strengthened. No layer skipped. |
| II — Prompt Content Ephemeral | ✅ PASS | JWT claims logged are `roles`, `team`, `sub`, `request_id` — organisational metadata, not prompt content. Raw token strings never persisted. FR-012 explicitly permits these fields per clarification Q3. |
| III — OpenAI Compatibility | ✅ PASS | `/v1/*` request and response schema unchanged. Only the `Authorization` header auth mechanism changes. Existing OpenAI-SDK clients continue to work by swapping API key for JWT. |
| IV — Defence in Depth | ✅ PASS | Kong (JWT auth) → OPA (policy, now receives `X-User-Roles` and `X-User-Team`) → Guardrails (content). All three layers preserved; OPA policy enforcement is strengthened with real role/team data. |
| V — Falsifiable Acceptance Criteria | ✅ PASS | SC-001–SC-009 all expressed as deterministic curl commands or log/metric queries. |

**No violations. Phase 0 may proceed.**

---

## Project Structure

### Documentation (this feature)

```text
specs/024-enterprise-sso-jwt/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
├── contracts/
│   ├── jwt-token.md     ← JWT token claim contract
│   ├── kong-routes.md   ← Kong plugin/route auth contract
│   └── upstream-headers.md ← Header forwarding contract
└── tasks.md             ← Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
services/keycloak/
└── realm-export.json              # inference-platform realm: clients, roles, mappers

services/kong/
├── kong.yml                       # Updated: jwt plugin replaces key-auth on /v1/*;
│                                  # Phoenix + Langfuse routes added
└── plugins/
    └── extract-jwt-claims.lua     # Post-function: X-User-Roles, X-User-Team, X-User-Sub

services/platform-ui/              # Next.js 15 — created/extended by this feature
├── auth.ts                        # next-auth v5 Keycloak OIDC provider config
├── middleware.ts                  # Protect all routes except /api/auth/*
└── app/api/auth/[...nextauth]/
    └── route.ts                   # next-auth route handler

scripts/
├── seed-kong-jwt.sh               # Fetch Keycloak realm RS256 public key → Kong jwt credential
└── smoke-test.sh                  # Updated: obtain JWT from Keycloak, use in curl tests

docker-compose.yml                 # Add keycloak service under [auth, all] profiles

.env.example                       # Add: KEYCLOAK_ADMIN, KEYCLOAK_ADMIN_PASSWORD,
                                   # KC_DB_PASSWORD, KEYCLOAK_REALM,
                                   # INFERENCE_GATEWAY_CLIENT_SECRET,
                                   # PLATFORM_UI_CLIENT_SECRET,
                                   # PHOENIX_UI_CLIENT_SECRET,
                                   # LANGFUSE_UI_CLIENT_SECRET,
                                   # NEXTAUTH_SECRET, NEXTAUTH_URL
```

**Structure Decision**: Configuration-first feature. No new Python service. Touches Kong (YAML + Lua), Keycloak (JSON realm), docker-compose (YAML), platform-ui (TypeScript), and Bash scripts. Each file is independently deployable and testable.

---

## Complexity Tracking

> No constitution violations — table not required.
