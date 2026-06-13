# Platform Progress

## Phase tracker

| # | Feature | Branch | Status |
|---|---|---|---|
| 001 | Dev env setup | 001-dev-env-setup | ✓ Done |
| 002 | Developer Makefile | 002-developer-makefile | ✓ Done |
| 003 | DB provisioning | 003-db-provisioning | ✓ Done |
| 004 | Model catalogue API | 004-model-catalogue-api | ✓ Done |
| 005 | Chat completion + OTel + Langfuse | 005-chat-completion-otel-langfuse | ✓ Done |
| 006 | Streaming SSE | 006-streaming-sse | ✓ Done |
| 007 | Response caching | 007-response-caching | ✓ Done |
| 008 | Model fallback routing | 008-model-fallback-routing | ✓ Done |
| 009 | Key budget + spend | 009-key-budget-spend | ✓ Done |
| 010 | Health endpoint | 010-health-endpoint | ✓ Done |
| 011 | Embeddings endpoint | 011-embeddings-endpoint | ✓ Done |
| 012 | Kong API gateway auth | 012-kong-api-gateway-auth | ✓ Done |
| 013 | Consumer rate limiting | 013-consumer-rate-limiting | ✓ Done |
| 014 | Request correlation | 014-request-correlation | ✓ Done |
| 015 | Gateway body size limit | 015-gateway-body-size-limit | ✓ Done |
| 016 | Gateway API versioning | 016-gateway-api-versioning | ✓ Done |
| 017 | Async batch inference | 017-async-batch-inference | ✓ Done |
| 018 | Multimodal image support | 018-multimodal-image-support | ✓ Done |
| 019 | Function calling | 019-function-calling | ✓ Done |
| 020 | Structured JSON output | 020-structured-json-output | ✓ Done |
| 021 | WebSocket streaming | 021-websocket-streaming | ✓ Done |
| 022 | Cache flush management | 022-cache-flush-management | ✓ Done |
| 023 | Guardrails bypass flag | 023-guardrails-bypass-flag | ✓ Done |
| 024 | Enterprise SSO JWT | 024-enterprise-sso-jwt | ✓ Done |
| **025** | **MFA TOTP Enforcement** | **025-mfa-totp-enforcement** | **🚧 In Progress** |

## Active feature: 025 — MFA TOTP Enforcement

**Spec**: `specs/025-mfa-totp-enforcement/spec.md`
**Plan**: `specs/025-mfa-totp-enforcement/plan.md`
**Tasks**: `specs/025-mfa-totp-enforcement/tasks.md`

### What ships in this feature

- `services/keycloak/realm-export.json`: `browser-mfa` authentication flow — `auth-username-password-form` REQUIRED → `auth-otp-form` CONDITIONAL (only for users with OTP configured or required by policy)
- OTP policy: RFC 6238 TOTP, HmacSHA1, 6-digit, 30-second window, `otpPolicyCodeReusable: false` (replay prevention), `otpPolicyLookAheadWindow: 1` (±30 s clock drift tolerance)
- Brute-force protection: `failureFactor: 5`, `permanentLockout: false`, 15-minute lockout on 5 consecutive OTP failures
- `browserFlow` binding updated to `"browser-mfa"` — all browser-based logins go through the new flow
- `defaultRequiredActions: []` — MFA optional by default; admins assign `CONFIGURE_TOTP` required action per user
- `scripts/smoke-test.sh`: four new `[025]` probes verifying browserFlow, OTP policy contract, brute-force config, and flow existence via Keycloak Admin API

### Runtime verification required (needs `make up-auth`)

Tasks T009–T026 require a running Keycloak instance. Complete these manually after `make up-auth`:
1. `make restart svc=keycloak` to import updated realm-export.json
2. Verify `browserFlow == "browser-mfa"` via Admin API (quickstart.md Step 1)
3. Create test user, assign `CONFIGURE_TOTP`, complete enrolment flow (quickstart.md Steps 2–3)
4. Verify returning-user TOTP challenge and wrong-code rejection (quickstart.md Step 4–5)
5. Verify 5-failure lockout via attack-detection endpoint (quickstart.md Step 6)
6. Run `make smoke` with `KEYCLOAK_ADMIN_PASSWORD` set — all `[025]` probes must pass

### Files changed

| File | Change |
|---|---|
| `services/keycloak/realm-export.json` | Added browser-mfa flow (3 auth flow entries), OTP policy, brute-force config, browserFlow binding |
| `scripts/smoke-test.sh` | Added `[025]` MFA config probes (4 checks via Keycloak Admin API) |

---

## Active feature (previous): 024 — Enterprise SSO JWT Validation

**Spec**: `specs/024-enterprise-sso-jwt/spec.md`
**Plan**: `specs/024-enterprise-sso-jwt/plan.md`
**Tasks**: `specs/024-enterprise-sso-jwt/tasks.md`

### What ships in this feature

- Keycloak 24 running under `auth` profile: realm `inference-platform`, RS256 tokens, 3600 s lifespan
- Realm roles: `admin`, `engineer`, `viewer`; protocol mappers emit `roles[]` and `team` claims
- Four OIDC clients: `inference-gateway` (client-credentials + PKCE), `platform-ui` (PKCE browser), `phoenix-ui` (bearer-only), `langfuse-ui` (bearer-only)
- Kong `jwt` plugin replaces `key-auth` on all `/v1/*` routes; `key_claim_name: iss` validates token issuer against Keycloak realm URL
- `scripts/seed-kong-jwt.sh`: fetches RS256 PEM from Keycloak, upserts three Kong JWT consumers idempotently; re-run after key rotation (SLO ≤5 min)
- Post-function Lua in Kong extracts `roles`, `team`, `sub` from validated JWT payload; sets `X-User-Roles`, `X-User-Team`, `X-User-Sub` upstream headers
- `request-transformer` plugin strips client-supplied `X-User-*` headers before the post-function sets them (prevents header spoofing)
- OPA policy (`services/opa/policies/inference.rego`): `viewer` blocked from non-GET; `engineer` full model access (no cache flush); `admin` unrestricted
- Guardrails service reads `X-User-Roles/Team/Sub` and includes `roles`, `team`, `sub` in structured audit log entries
- Phoenix UI (`/phoenix`) and Langfuse UI (`/langfuse`) protected by separate `jwt` plugin instances using `key_claim_name: aud`
- Global `post-function` body_filter rewrites Kong 401/403 responses to platform error schema `{"error": ..., "message": ..., "detail": {"reason": ...}}`
- Platform UI (`services/platform-ui/`): Next.js 15 + next-auth v5 shell with Keycloak provider; `session.user.roles`, `session.user.team`, `session.user.access_token` populated from JWT claims

### Files changed

| File | Change |
|---|---|
| `services/keycloak/realm-export.json` | New — realm config with roles, 4 OIDC clients, protocol mappers |
| `services/kong/kong.yml` | Replaced key-auth with jwt plugin; added Lua claim-extraction post-function; phoenix-ui and langfuse-ui routes; consumers; global 401/403 error rewriter |
| `services/opa/policies/inference.rego` | New — ABAC allow rules for admin/engineer/viewer roles |
| `services/guardrails/main.py` | Reads X-User-Roles/Team/Sub; appends roles/team/sub to audit log entries |
| `services/platform-ui/` | New — Next.js 15 minimal shell: package.json, auth.ts (next-auth v5), middleware.ts, route handler, type augmentations |
| `scripts/seed-kong-jwt.sh` | New — idempotent JWT consumer seeding script |
| `scripts/smoke-test.sh` | Added JWT token acquisition, 024 probes (malformed/unknown-iss/valid/UI routes/key-rotation) |
| `docker-compose.yml` | Added Keycloak service (auth profile), keycloak_data volume |
| `.env.example` | Added KEYCLOAK_ADMIN, KEYCLOAK_REALM, client secrets, NEXTAUTH vars |
| `Makefile` | Added seed-kong-jwt target |

---

## Feature 023 — Guardrails Bypass Flag

**Spec**: `specs/023-guardrails-bypass-flag/spec.md`
**Plan**: `specs/023-guardrails-bypass-flag/plan.md`
**Tasks**: `specs/023-guardrails-bypass-flag/tasks.md`

### What ships in this feature

- Optional `guardrails` boolean field on `POST /v1/chat/completions` (default: `true`)
- When `guardrails: false` + `stream: true`: `_streaming_passthrough()` uses `httpx client.stream()` — SSE chunks forwarded in real time, no buffering
- When `guardrails: false` + `stream: false`: direct buffered passthrough, all validation gates skipped
- When `guardrails: true` or absent: all existing validation behaviour unchanged (zero regression)
- Flag stripped from request body before forwarding to LiteLLM
- Audit log entry written on all paths including bypass
- Bug fixes: portal-backend `REDIS_URL` env var + `seed-kong.sh` cache-flush route registration (022 post-ship)
- Reference client `app/test.py`: httpx streaming via explicit `/v1/chat/completions` endpoint, python-dotenv key loading, SSE error chunk handling

### Files changed

| File | Change |
|---|---|
| `services/guardrails/main.py` | Added `_streaming_passthrough()` helper; added guardrails bypass flag extraction and routing in `proxy()` |
| `docker-compose.yml` | Added `REDIS_URL: redis://redis-cache:6379` and `redis-cache` dependency to portal-backend (bug fix 022) |
| `scripts/seed-kong.sh` | Added `DELETE /cache/flush` route registration to `create_admin_services()` (bug fix 022) |
| `app/test.py` | New — reference streaming client with httpx, python-dotenv, guardrails bypass, SSE error handling |

---

## Feature 022 — Cache Flush Management

**Spec**: `specs/022-cache-flush-management/spec.md`
**Plan**: `specs/022-cache-flush-management/plan.md`
**Tasks**: `specs/022-cache-flush-management/tasks.md`

### What ships in this feature

- `DELETE /cache/flush` — flush all `llm_cache:*` Redis entries (master key only, 10 RPM rate-limit)
- `DELETE /cache/flush?model={name}` — flush only entries for the named model; validates against LiteLLM catalogue; returns HTTP 422 with `valid_models` on unknown name
- Implemented in `portal-backend` service using `redis.asyncio` SCAN+DEL (non-blocking, 100-key batches)
- Returns `{"keys_deleted": <int>, "model": <str|null>}`
- Audit log per flush: `cache_flush_all` or `cache_flush_model` event type
- Two post-ship bug fixes (T018, T019): missing `REDIS_URL` env var and missing Kong route in seed script

### Files changed

| File | Change |
|---|---|
| `services/portal-backend/main.py` | Added `/cache/flush` endpoint, `_redis_scan_del()`, `_validate_master_key()`, `_write_cache_audit()`, `CacheFlushResult` model |
| `services/portal-backend/requirements.txt` | Added `redis[asyncio]==5.0.8` |
| `services/kong/kong.yml` | Added `cache-flush` route + key-auth + rate-limiting plugins to portal-backend service |
| `scripts/seed-kong.sh` | Added `DELETE /cache/flush` route registration (post-ship fix) |
| `docker-compose.yml` | Added `REDIS_URL` env var and `redis-cache` dependency to portal-backend (post-ship fix) |

---

## Feature 017 — Async Batch Inference

**Spec**: `specs/017-async-batch-inference/spec.md`
**Plan**: `specs/017-async-batch-inference/plan.md`
**Tasks**: `specs/017-async-batch-inference/tasks.md`

### What ships in this feature

- FastAPI `batch-api` service on port 8091 (internal, behind Kong :8080)
- RQ `batch-worker` processes jobs from redis-queue (port 6380, noeviction)
- `POST /v1/batch/jobs` → 202 with `job_id` within 1 second (up to 10,000 items per batch)
- `GET /v1/batch/jobs/{id}` → status polling (`queued` → `running` → `completed`)
- `GET /v1/batch/jobs/{id}/results` → JSONL streaming response sorted by item index
- Per-item retry: up to 3 attempts with exponential backoff (1 s, 2 s) on 5xx/network errors
- `MAX_CONCURRENT` asyncio semaphore caps parallel LiteLLM calls (default 4, env var)
- OTel parent span per job + child span per item exported to OTel Collector :4318
- 24-hour result retention with APScheduler auto-deletion (every 5 min)
- New 7th PostgreSQL database: `batch` (schema-isolated from litellm)
- Constitution §2.4 justified exception: `input_payload` NULLed immediately after each item processes

### Files changed

| File | Change |
|---|---|
| `services/batch-worker/Dockerfile` | New — multi-stage, non-root, linux/amd64+arm64 |
| `services/batch-worker/requirements.txt` | New — pinned deps: fastapi, rq, asyncpg, httpx, opentelemetry-sdk, apscheduler |
| `services/batch-worker/main.py` | New — FastAPI app with all 3 endpoints + lifespan |
| `services/batch-worker/worker.py` | New — RQ job + async item processor + retry + semaphore + OTel |
| `services/batch-worker/db.py` | New — asyncpg pool + DDL + CRUD helpers |
| `services/batch-worker/otel.py` | New — TracerProvider, OTLP exporter, context inject/extract |
| `services/batch-worker/cleanup.py` | New — APScheduler jobs for result expiry + payload nulling |
| `scripts/init-db.sql` | Added 7th database: `batch` |
| `scripts/seed-kong.sh` | Added `create_batch_service()` + 3 Kong routes |
| `services/kong/kong.yml` | Added batch-api service + 3 routes (reference doc) |
| `docker-compose.yml` | Added `batch-api` + `batch-worker` services + `batch_results` volume |
| `.env.example` | Added 5 batch env vars |
| `scripts/smoke-test.sh` | Added 6 batch API probes |
| `docs/progress.md` | This file — updated |

## Feature 015 — Gateway Body Size Limit

**Spec**: `specs/015-gateway-body-size-limit/spec.md`
**Plan**: `specs/015-gateway-body-size-limit/plan.md`
**Tasks**: `specs/015-gateway-body-size-limit/tasks.md`

### What ships in this feature

- Kong `pre-function` plugin strips client-supplied `X-Request-ID`, `traceparent`, `tracestate`
- Kong `correlation-id` plugin generates UUID per request, echoed in response as `X-Request-ID`
- Kong `opentelemetry` plugin generates W3C `traceparent`/`tracestate` and emits spans to OTel Collector
- OTel Collector (`obs` profile) enriches spans with `gateway.request_id` attribute, forwards to Phoenix Arize
- LiteLLM OTLP export rerouted through Collector (`OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces`)
- Guardrails service writes structured audit log entry per request including `request_id`
- 7 new smoke probes covering SC-001 through SC-004

### Files changed

| File | Change |
|---|---|
| `services/kong/kong.yml` | Added `pre-function` + `opentelemetry` global plugins |
| `services/otel/otel-collector.yml` | New — OTel Collector pipeline config |
| `docker-compose.yml` | Added `otel-collector` service (obs profile) |
| `.env.example` | Updated OTel endpoint comment |
| `services/guardrails/main.py` | Added `_write_audit` structured log function |
| `scripts/smoke-test.sh` | Added 7 request correlation probes |
| `docs/progress.md` | This file — created |

## Feature 015 — Gateway Body Size Limit

**Spec**: `specs/015-gateway-body-size-limit/spec.md`
**Plan**: `specs/015-gateway-body-size-limit/plan.md`
**Tasks**: `specs/015-gateway-body-size-limit/tasks.md`

### What ships in this feature

- Kong `request-size-limiting` global plugin — rejects payloads > `REQUEST_SIZE_LIMIT_MB` MB (default 10) with HTTP 413
- Applied to all routes (global scope) — no per-route configuration required
- Rejection occurs at Kong edge before any upstream is contacted (Guardrails, LiteLLM)
- 5 new smoke probes: oversized rejection (chat + embeddings), boundary pass-through, regression check, header assertion

### Files changed

| File | Change |
|---|---|
| `scripts/seed-kong.sh` | Added `create_request_size_plugin()` function + `REQUEST_SIZE_LIMIT_MB` env var |
| `services/kong/kong.yml` | Added `request-size-limiting` global plugin with `allowed_payload_size: 10` |
| `.env.example` | Added `REQUEST_SIZE_LIMIT_MB=` variable |
| `scripts/smoke-test.sh` | Added 5 request-size-limit probes (US1, US2, US3) |
| `docs/progress.md` | Updated active feature tracker |
