# Implementation Plan: Request Correlation

**Branch**: `014-request-correlation` | **Date**: 2026-06-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/014-request-correlation/spec.md`

---

## Summary

Assign a gateway-generated UUID (`X-Request-ID`) to every inbound request at Kong. Strip any client-supplied correlation or trace headers before ID generation. Forward the UUID and W3C `traceparent`/`tracestate` headers to all downstream services (guardrails, LiteLLM). Return the UUID to the caller on every response. Deploy an OTel Collector to enrich spans with the UUID as a `gateway.request_id` trace attribute so Phoenix Arize can correlate gateway activity with LLM spans via a single identifier.

---

## Technical Context

**Language/Version**: Lua (Kong pre-function), YAML (Kong config, OTel Collector config), Bash (smoke tests)

**Primary Dependencies**:
- Kong 3.6 — `correlation-id` (existing), `pre-function`, `opentelemetry` plugins
- OTel Collector `otel/opentelemetry-collector-contrib:0.97.0` (new service, `obs` profile)
- Phoenix Arize `arizephoenix/phoenix:latest` (existing, receives enriched spans)

**Storage**: None — correlation data is ephemeral (headers, OTel spans, structured log fields)

**Testing**: Bash `scripts/smoke-test.sh` (curl probes for header presence, UUID format, client overwrite, downstream forwarding)

**Target Platform**: Docker Compose `obs` profile (OTel Collector joins existing `core` + `obs` services)

**Performance Goals**: Zero measurable p99 latency addition (SC-006); header generation and OTel export are async

**Constraints**:
- `pre-function` plugin must run at priority > 100001 (before `correlation-id`)
- OTel export is non-blocking — span emission must not add to request latency
- No prompt content in any span attribute (constitution §II)
- OTel Collector must be in `obs` profile only — `core` profile must work without it

**Scale/Scope**: Every request through Kong :8080 — ~100% traffic coverage

---

## Constitution Check

| Principle | Gate | Status |
|---|---|---|
| I. Request Flow Integrity | No bypassing Kong → Guardrails → LiteLLM | PASS — changes are Kong-edge config only |
| II. Prompt Content Ephemeral | No prompt/response in headers or span attributes | PASS — UUID is opaque; `gateway.request_id` carries UUID only |
| III. OpenAI API Compatibility | X-Request-ID already mandated in §4.3 | PASS — this feature delivers what §4.3 requires |
| IV. Defence in Depth | Kong edge layer strengthened | PASS — pre-function adds stripping; no enforcement layer weakened |
| V. Falsifiable Acceptance Criteria | All SC entries are curl-verifiable | PASS — smoke probes map 1:1 to each SC |

**Post-design re-check**: OTel Collector added to `obs` profile only; `core` profile unchanged. No memory budget impact on core-only runs. ✓

---

## Project Structure

### Documentation (this feature)

```text
specs/014-request-correlation/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 — plugin execution order, Kong OTel, Collector enrichment
├── data-model.md        # Phase 1 — header flow, span attribute schema, log field schema
├── quickstart.md        # Phase 1 — verification commands
├── contracts/
│   └── response-headers.md   # Phase 1 — X-Request-ID contract + verification
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code

```text
services/
├── kong/
│   └── kong.yml                   # MODIFY — add pre-function + opentelemetry global plugins
└── otel/
    └── otel-collector.yml         # NEW — OTel Collector pipeline config

docker-compose.yml                 # MODIFY — add otel-collector service under obs profile

scripts/
└── smoke-test.sh                  # MODIFY — add request correlation smoke probes
```

**Structure Decision**: Pure configuration feature — no new application code, no new containers beyond OTel Collector. All changes are YAML config files and a Bash test extension.

---

## Implementation Detail

### 1. `services/kong/kong.yml` — Global Plugins additions

**Add before existing `correlation-id` plugin** (execution order ensured by Kong priority, not YAML ordering):

```yaml
  # pre-function: strip client-supplied correlation/trace headers
  # Priority 1000000 — runs before correlation-id (100001) and opentelemetry (100000)
  - name: pre-function
    config:
      access:
        - |
          kong.request.clear_header("X-Request-ID")
          kong.request.clear_header("traceparent")
          kong.request.clear_header("tracestate")

  # correlation-id: generate UUID, echo to caller
  # (already present — no changes needed)
  - name: correlation-id
    config:
      header_name:     X-Request-ID
      generator:       uuid
      echo_downstream: true

  # opentelemetry: generate W3C traceparent/tracestate, emit spans to OTel Collector
  - name: opentelemetry
    config:
      endpoint: http://otel-collector:4318/v1/traces
      resource_attributes:
        service.name: kong-gateway
      propagation:
        default_format: w3c
      batch_span_count: 200
      batch_flush_delay: 3
```

### 2. `services/otel/otel-collector.yml` — New file

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  attributes:
    actions:
      - key: gateway.request_id
        from_attribute: http.request.header.x_request_id
        action: insert
  batch:
    timeout: 5s
    send_batch_size: 512

exporters:
  otlp/phoenix:
    endpoint: http://arize-phoenix:6006
    tls:
      insecure: true

service:
  pipelines:
    traces:
      receivers:  [otlp]
      processors: [attributes, batch]
      exporters:  [otlp/phoenix]
```

### 3. `docker-compose.yml` — Add otel-collector service

Add under the `obs` profile, before `arize-phoenix`:

```yaml
  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.97.0
    profiles: [obs]
    command: ["--config=/etc/otelcol/otel-collector.yml"]
    volumes:
      - ./services/otel/otel-collector.yml:/etc/otelcol/otel-collector.yml:ro
    expose:
      - "4317"
      - "4318"
    depends_on:
      arize-phoenix:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://localhost:13133/"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    restart: unless-stopped
```

Update `arize-phoenix` — no change needed (still listens on 6006 for OTLP from Collector).

Update LiteLLM environment variable (in docker-compose.yml or `.env`):
```
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces
```
(was: `http://arize-phoenix:6006/v1/traces`)

### 4. `scripts/smoke-test.sh` — New probes section

```bash
# ── Request correlation probes (feature 014) ──────────────────────────────────

# T001: X-Request-ID present on every response (SC-001, FR-003)
req_id=$(curl -si --max-time "$TIMEOUT" \
  "${KONG}/health" 2>/dev/null | tr -d '\r' \
  | grep -i "^x-request-id:" | awk '{print $2}')
if [[ -n "$req_id" ]]; then
  ok "X-Request-ID — present in response header (${req_id})"
else
  fail "X-Request-ID — missing from response header (FR-003 violation)"
fi

# T002: X-Request-ID is UUID v4 format (SC-001, FR-001)
uuid_regex='^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
if [[ -n "$req_id" ]] && echo "$req_id" | grep -Eqi "$uuid_regex"; then
  ok "X-Request-ID — UUID v4 format confirmed"
else
  fail "X-Request-ID — not a valid UUID v4: '${req_id:-absent}'"
fi

# T003: client-supplied X-Request-ID is overwritten (SC-004, FR-002)
client_id="00000000-0000-0000-0000-000000000000"
returned_id=$(curl -si --max-time "$TIMEOUT" \
  -H "X-Request-ID: ${client_id}" \
  "${KONG}/health" 2>/dev/null | tr -d '\r' \
  | grep -i "^x-request-id:" | awk '{print $2}')
if [[ "$returned_id" != "$client_id" && -n "$returned_id" ]]; then
  ok "X-Request-ID — client-supplied value overwritten (returned: ${returned_id})"
else
  fail "X-Request-ID — client value leaked to response: '${returned_id}'"
fi

# T004: X-Request-ID unique across sequential requests (SC-001, FR-007)
id_a=$(curl -si --max-time "$TIMEOUT" "${KONG}/health" 2>/dev/null | tr -d '\r' \
  | grep -i "^x-request-id:" | awk '{print $2}')
id_b=$(curl -si --max-time "$TIMEOUT" "${KONG}/health" 2>/dev/null | tr -d '\r' \
  | grep -i "^x-request-id:" | awk '{print $2}')
if [[ -n "$id_a" && -n "$id_b" && "$id_a" != "$id_b" ]]; then
  ok "X-Request-ID — unique across sequential requests (${id_a} ≠ ${id_b})"
else
  fail "X-Request-ID — duplicate or empty IDs: a='${id_a}' b='${id_b}'"
fi

# T005: X-Request-ID present on 401 error response (SC-001, FR-003)
err_id=$(curl -si --max-time "$TIMEOUT" \
  "${KONG}/v1/chat/completions" \
  -X POST -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"no auth"}]}' \
  2>/dev/null | tr -d '\r' \
  | grep -i "^x-request-id:" | awk '{print $2}')
if [[ -n "$err_id" ]]; then
  ok "X-Request-ID — present on 401 error response (${err_id})"
else
  fail "X-Request-ID — missing from 401 error response (FR-003 violation)"
fi
```

---

## Complexity Tracking

No constitution violations. No complexity justification required.

---

## Acceptance Criteria (deterministic, from spec SC entries)

| ID | Test | Pass condition |
|---|---|---|
| SC-001 | T001 smoke probe | `X-Request-ID` header non-empty on 1,000 consecutive responses |
| SC-001 | T002 smoke probe | UUID v4 regex matches on every response |
| SC-002 | T001 + gateway log | UUID in access log matches response header value |
| SC-003 | Phoenix query | Span with `gateway.request_id = <UUID>` visible within 30s |
| SC-004 | T003 smoke probe | Zero client-supplied IDs returned in response |
| SC-005 | Manual | Single UUID lookup in Loki + Phoenix returns all correlated records |
| SC-006 | p99 comparison | No measurable latency increase vs. baseline over 500 requests |
