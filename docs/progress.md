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
| **015** | **Gateway body size limit** | **015-gateway-body-size-limit** | **⚡ Active** |

## Active feature: 015 — Gateway Body Size Limit

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
