# Implementation Plan: Cache Flush Management

**Branch**: `022-cache-flush-management` | **Date**: 2026-06-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/022-cache-flush-management/spec.md`

## Summary

Expose two admin endpoints — `DELETE /cache/flush` (full) and `DELETE /cache/flush?model={name}` (scoped) — that let platform operators invalidate LiteLLM's Redis response cache after model configuration changes or prompt updates. Implemented as a surgical addition to the existing portal-backend service. Backed by Redis SCAN+DEL on the `llm_cache:` key namespace. Routed through Kong with `LITELLM_MASTER_KEY` authentication and a 10 RPM rate-limit plugin. Returns `{"keys_deleted": <int>}`.

## Technical Context

**Language/Version**: Python 3.11 — FastAPI (same as all other Python services)

**Primary Dependencies**:
- `fastapi` — existing in portal-backend
- `httpx[async]` — existing in portal-backend (used for model catalogue validation via `GET /v1/models`)
- `redis[asyncio]` — **new dependency** for Redis SCAN+DEL
- `opentelemetry-sdk` — existing (audit logging to Loki)

**Storage**: Redis 6379 — SCAN+DEL on `llm_cache:` prefix. Redis 6380 (batch queue) is untouched.

**Testing**: pytest + `httpx.AsyncClient` for contract tests; `curl` for smoke tests

**Target Platform**: Docker Compose `core` profile (portal-backend is in the `portal` profile; endpoints only require `core` stack to be up for Redis + portal-backend)

**Performance Goals**:
- Full flush: < 5 s (SC-001)
- Non-master key rejection: < 100 ms before any Redis operation (SC-003)
- Model-scoped flush: < 2 s for typical cache sizes

**Constraints**:
- `LITELLM_MASTER_KEY` sourced from Vault; available as env var at runtime
- No prompt content or raw cache key contents in any log entry (constitution §II)
- All traffic enters via Kong :8080 (no direct portal-backend access)
- Redis SCAN is non-blocking (no `FLUSHDB` — would destroy non-cache keys)
- `redis[asyncio]` is the only new package added to this service

**Scale/Scope**: Low-frequency admin operation (< 10 calls/day expected). Cache may hold up to 100,000 keys at peak; SCAN+DEL handles this non-blockingly in batches of 100.

## Constitution Check

*Re-evaluated after Phase 1 design.*

### Principle I — Request Flow Integrity ✅ PASS (admin endpoint exception, not a deviation)

Cache flush is an **admin/operational endpoint**, not an inference request. The inference chain `Kong → Guardrails → LiteLLM` applies to inference traffic. The cache flush route is `Kong :8080 → portal-backend :8092 → Redis :6379` — Kong remains the sole external entry point.

**Precedent established**: portal-backend already calls LiteLLM directly for spend data (`/global/spend/keys`, `/global/spend/models`). Kong already has a direct `litellm-admin` route for `/v1/key` that bypasses Guardrails. Admin endpoints have always been separate from the inference chain.

**No ADR required** — this follows the established admin-endpoint pattern; no inference chain is modified.

### Principle II — Prompt Content is Ephemeral ✅ PASS

Cache flush operates on Redis key names (hashed request fingerprints). The `keys_deleted` count is pure metadata. Audit entries contain only: `timestamp`, `event_type`, `request_id`, `key_hash`, `model`, `keys_deleted`, `status_code`. No cache key names, no prompt content, no raw credential values are logged.

### Principle III — OpenAI API Compatibility ✅ PASS (not applicable)

Cache flush lives at `/cache/flush` (no `/v1/` prefix). It is not an inference endpoint and is not required to follow the OpenAI schema. It does not modify the `/v1/` stable surface.

### Principle IV — Defence in Depth ✅ PASS

- **Kong (edge)**: `key-auth` plugin validates a known consumer key before forwarding; `rate-limiting` plugin (10 RPM) prevents accidental rapid re-calls
- **portal-backend (application)**: secondary check that the key is specifically `LITELLM_MASTER_KEY` (Kong validates presence; app validates identity)
- **OPA**: not in path — master key identity check is simpler than ABAC
- **Guardrails**: not in path — no content scanning required for admin operations

### Principle V — Falsifiable Acceptance Criteria ✅ PASS

All SC-001–SC-006 are expressed as `curl` commands with exact HTTP status codes and response body fields. SC-004 uses a Loki query. All criteria are in `quickstart.md`.

## Project Structure

### Documentation (this feature)

```text
specs/022-cache-flush-management/
├── plan.md                         # This file
├── research.md                     # 8 architectural decisions
├── data-model.md                   # Entities + Redis key namespace
├── quickstart.md                   # 7 curl-based acceptance tests
├── contracts/
│   └── cache-flush-api.md          # Full API contract for both endpoints
└── tasks.md                        # Phase 2 output (/speckit-tasks — not yet created)
```

### Source Code (changes to existing files only)

```text
services/portal-backend/
└── main.py              # Add /cache/flush endpoint handler (surgical addition)
                         # Add redis[asyncio] client + SCAN+DEL helpers
                         # Add master key validation helper
                         # Add audit log helper

services/kong/
└── kong.yml             # Add cache-flush route to portal-backend service entry
                         # Add key-auth (hide_credentials: false) + rate-limiting plugins

tests/
├── contract/
│   └── test_cache_flush.py     # NEW — 6 pytest-asyncio contract tests
└── smoke/
    └── test_cache_flush.sh     # NEW — curl-based smoke tests (Tests 1–7 from quickstart.md)

.env.example             # No new vars needed (LITELLM_MASTER_KEY already present)
```

**Structure Decision**: Zero new containers. Zero new Dockerfile changes. Zero new docker-compose entries. Two existing files modified (`main.py`, `kong.yml`). Two new test files. `redis[asyncio]` added to portal-backend's runtime dependencies.

## Task Groups

### Group A — Setup (no dependencies)

- Kong route + plugins for `/cache/flush` in `kong.yml`
- `redis[asyncio]` dependency declaration (requirements.txt or Dockerfile)

### Group B — Foundational (blocks all user stories)

- Master key validation helper in `main.py`
- Redis SCAN+DEL helper functions in `main.py`
- Audit log helper in `main.py`

### Group C — User Stories (after Group B)

- **US1 (P1)**: `DELETE /cache/flush` full flush endpoint
- **US2 (P2)**: `DELETE /cache/flush?model={name}` scoped flush endpoint + model catalogue validation

### Group D — Polish (after Group C)

- Contract tests (`test_cache_flush.py`)
- Smoke test script (`test_cache_flush.sh`)
- `ruff check` + `mypy` pass

## Acceptance Criteria

| Criterion | Verification Command |
|---|---|
| SC-001: Full flush < 5 s | `time curl -X DELETE http://localhost:8080/cache/flush -H "Authorization: Bearer $SMOKE_API_KEY"` |
| SC-002: Scoped flush removes only target model | Populate two models → flush one → re-issue both; only flushed model is a cache miss |
| SC-003: Non-master rejected < 100 ms | `time curl -X DELETE http://localhost:8080/cache/flush -H "Authorization: Bearer sk-non-master"` → HTTP 403 |
| SC-004: 100% audit coverage | Loki query: `{service="portal-backend"} \|= "cache_flush"` after every flush |
| SC-005: 422 + valid model list | `curl -X DELETE "http://localhost:8080/cache/flush?model=bad-name"` → HTTP 422 with `valid_models` |
| SC-006: 0% stale cache hits post-flush | Re-issue previously-cached request → cache miss (fresh LLM call) |
