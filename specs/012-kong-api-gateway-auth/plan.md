# Implementation Plan: API Gateway Authentication

**Branch**: `012-kong-api-gateway-auth` | **Date**: 2026-05-30 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/012-kong-api-gateway-auth/spec.md`

---

## Summary

Switch Kong from DB-less (declarative) mode to PostgreSQL-backed mode so that all consumer, service, route, and plugin configuration is managed at runtime via the Admin API. A one-shot `kong-migration` container runs `kong migrations bootstrap` before Kong starts. `seed-kong.sh` is rewritten to create all services, routes, consumers, and plugins via the Admin API (port 8001). A global `key-auth` plugin on the `Authorization` header secures all inference endpoints; the `/health` route is exempted using Kong's anonymous-consumer pattern. LiteLLM port 4000 retains its existing no-host-binding configuration (already correct per constitution §2.1).

---

## Technical Context

**Gateway**: Kong 3.6 (`kong:3.6`)

**Auth Plugin**: `key-auth` — global scope, `key_names: [Authorization]`, `hide_credentials: true`, `anonymous: <anon-consumer-uuid>`

**Database**: PostgreSQL 16 — existing `kong` database (created by `scripts/init-db.sql`). Kong switches from `KONG_DATABASE=off` (DB-less) to `KONG_DATABASE=postgres`.

**Migration**: `kong-migration` one-shot container runs `kong migrations bootstrap && kong migrations up` on first start. Kong service depends on migration completing (`service_completed_successfully`).

**Seeding**: `scripts/seed-kong.sh` rewritten to call Kong Admin API (`:8001`) to create all services, routes, consumers, plugins, and credentials. Environment variable `SMOKE_API_KEY` provides the smoke-test consumer key.

**Port binding**: Kong proxy `:8080` (public), Kong admin `127.0.0.1:8001` (localhost only). LiteLLM `:4000` — internal only, no host binding (unchanged).

**Testing**: `scripts/smoke-test.sh` extended with auth-rejection assertions (`curl` returning HTTP 401).

**Target Platform**: Docker Compose `core` profile on macOS (OrbStack).

**Performance Goals**: Auth rejection (401) delivered in under 200 ms at the client (SC-002). Health probe under 2 s (SC-003).

**Constraints**: No host binding on any internal port except Kong `:8080`. `make stats` must show `core` profile within ~620 MB limit.

**Scale/Scope**: Single-node local development. Helm values for GKE in Phase 10 are out of scope.

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I — Request Flow Integrity | PASS | Kong :8080 remains the only externally accessible port. LiteLLM :4000 retains no host binding. Chain is unchanged. |
| II — Prompt Content Ephemeral | PASS | This feature touches gateway config only — no prompt content path involved. |
| III — OpenAI API Compatibility | PASS | `/v1` stable surface unchanged. `/v2` route added as a forward-proxy alias for future use; no breaking changes. All responses continue to carry `X-Request-ID`, `X-Platform`, `X-API-Version` headers. |
| IV — Defence in Depth | PASS | Kong is enforcement layer 1. Key-auth global plugin strengthens the edge. Anonymous-consumer pattern for health is a standard Kong pattern and does not weaken auth on inference paths. |
| V — Falsifiable Acceptance Criteria | PASS | All acceptance criteria expressed as `curl` commands with expected HTTP status codes. |

**No violations. No Complexity Tracking entry required.**

---

## Project Structure

### Documentation (this feature)

```text
specs/012-kong-api-gateway-auth/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
├── contracts/
│   └── gateway-api.md   ← Phase 1 output
└── tasks.md             ← Phase 2 output (created by /speckit-tasks)
```

### Source Code (repository root)

```text
docker-compose.yml               ← Kong service: add PG env vars + kong-migration service
scripts/
├── seed-kong.sh                 ← rewrite: full Admin API seeding (services, routes, consumers, plugins)
├── smoke-test.sh                ← extend: add 401-rejection assertions
└── init-db.sql                  ← no change (kong DB already created)
services/kong/
└── kong.yml                     ← retained as reference; no longer loaded at runtime
.env.example                     ← add KONG_PG_PASSWORD, KONG_PG_USER, KONG_PG_DATABASE if absent
Makefile                         ← no change (make seed-kong target already correct)
```

**Structure Decision**: Infrastructure/config feature — no new Python or TypeScript source. Changes are confined to Docker Compose service definitions and shell scripts.
