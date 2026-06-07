# Implementation Plan: WebSocket Streaming

**Branch**: `021-websocket-streaming` | **Date**: 2026-06-07 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/021-websocket-streaming/spec.md`

---

## Summary

Add a persistent bidirectional WebSocket endpoint at `/ws/v1/chat/completions`
that streams OpenAI-format token deltas to callers. A caller connects once,
sends chat completion payloads as JSON frames, receives token deltas in real
time, and can submit further requests over the same connection after each
`stream_complete` sentinel.

**Approach**: Extend the existing **guardrails service** (FastAPI) with a
`@app.websocket` handler that accepts WS connections, applies all existing
validation gates (vision, function-calling, structured-output, PII), bridges
each request to LiteLLM as an HTTP SSE call, and forwards SSE chunks as
discrete WebSocket JSON frames. Kong routes the WS upgrade to guardrails with
120 s read/write timeouts. No new service. No LiteLLM fork.

---

## Technical Context

**Language/Version**: Python 3.11 (existing guardrails service)

**Primary Dependencies**:
- FastAPI 0.115 + Starlette WebSocket (`uvicorn[standard]` already installed — provides `websockets`)
- `httpx 0.27` with streaming (`AsyncClient` already in service)
- OpenTelemetry SDK (already instrumented in guardrails)
- Kong 3.6 (existing gateway — supports WS upgrade passthrough on `http` protocol services)

**Storage**: None (runtime-only state in `app.state`; no new DB tables)

**Testing**: `pytest` (existing) + `wscat` CLI for smoke/contract tests

**Target Platform**: Docker Compose `core` profile (existing guardrails container, port 8088 internal)

**Performance Goals**: First token within 600 ms p95 (SC-001); 200 concurrent streams within existing memory budget (SC-007)

**Constraints**: Must not bypass Kong → Guardrails → LiteLLM chain; prompt content never persisted; sequential requests per connection (no multiplexing in v1)

**Scale/Scope**: 200 concurrent active streams; 10 connections per API key (default, configurable)

---

## Constitution Check

*Gate: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I — Request flow integrity | ✅ PASS | Client → Kong:8080 (WS upgrade + key-auth) → Guardrails:8088 (WS handler + validation gates + PII) → LiteLLM:4000 (HTTP SSE). Chain is preserved end-to-end. |
| II — Prompt content ephemeral | ✅ PASS | Audit log writes only metadata fields (event_type, request_id, key_hash, model, pii_count, scanner_blocked). OTel spans carry no input/output text. |
| III — OpenAI compatibility | ✅ PASS | Token delta frames are unmodified OpenAI streaming chunk JSON. Client → server payload is standard OpenAI chat completion request body. |
| IV — Defence in depth (3 layers) | ✅ PASS | Layer 1 (Kong): key-auth on WS upgrade, rate-limit, request-size. Layer 2 (OPA): caller's model-scope enforced via existing key identity. Layer 3 (Guardrails): vision/FC/SO gates + PII redaction applied per WS message before LiteLLM call. |
| V — Falsifiable acceptance criteria | ✅ PASS | All SC-001–SC-009 expressed as `wscat` commands, `docker stats` checks, or Loki/Prometheus queries. See quickstart.md. |

**Post-design re-check**: No new services, no new ports (guardrails already on 8088 internal). Memory impact is WS connection state in `app.state` (~1 KB per connection × 200 = ~200 KB, negligible within core-profile budget of ~620 MB).

---

## Project Structure

### Documentation (this feature)

```text
specs/021-websocket-streaming/
├── plan.md                           # This file
├── research.md                       # Phase 0 — all decisions resolved
├── data-model.md                     # Phase 1 — entities, wire formats, env vars
├── quickstart.md                     # Phase 1 — wscat test guide
├── contracts/
│   └── websocket-protocol.md         # Phase 1 — client-facing protocol contract
├── checklists/
│   └── requirements.md               # Spec quality checklist
└── tasks.md                          # Phase 2 output (created by /speckit-tasks)
```

### Source Code Changes

```text
services/guardrails/
├── main.py                    # Modified: register WS route + connection-limit state
├── websocket.py               # New: WS handler, SSE bridge, connection manager
└── requirements.txt           # Unchanged (uvicorn[standard] already provides websockets)

services/kong/
└── kong.yml                   # Modified: add litellm-ws service + ws-chat-completions route

docker-compose.yml             # Modified: add WS_MAX_CONNECTIONS_PER_KEY +
                               #           WS_IDLE_TIMEOUT_SECONDS to guardrails env

tests/smoke/
└── test_websocket_streaming.sh  # New: non-interactive wscat smoke test (Tests 1–4)

tests/contract/
└── test_ws_streaming.py         # New: pytest contract tests (connection lifecycle,
                                 #      stream-in-progress rejection, error formats)
```

**Structure decision**: Single-service extension. No new service or Docker profile.
The WebSocket handler is a new module (`websocket.py`) in the guardrails package,
registered via `app.include_router` or direct `@app.websocket` in `main.py`.

---

## Implementation Breakdown

### Task Group A — Guardrails: WebSocket handler (`websocket.py`)

**A1. Connection manager** (`websocket.py`)
- `app.state.ws_connections: dict[str, int]` and `app.state.ws_lock: asyncio.Lock`
  initialised in `_lifespan()` in `main.py`.
- `async def acquire_connection(consumer_id, max_conns) → bool`: atomically
  increments and returns `False` if limit reached.
- `async def release_connection(consumer_id)`: decrements on disconnect.

**A2. WS handler entry point** (`websocket.py`)
```python
@app.websocket("/ws/v1/chat/completions")
async def ws_chat_completions(websocket: WebSocket) -> None
```
- Reads `X-Consumer-Username` from `websocket.headers`.
- Calls `acquire_connection`; sends `connection_limit_exceeded` and closes with
  4029 if limit reached.
- Calls `await websocket.accept()`.
- Enters the message-receive loop with `asyncio.wait_for(..., WS_IDLE_TIMEOUT_SECONDS)`.
- On `asyncio.TimeoutError`: sends `connection_closing`, closes 1001.
- On `WebSocketDisconnect`: calls `release_connection`, exits.
- Finally block always calls `release_connection`.

**A3. Per-message stream handler** (`websocket.py`)
- Parses the received JSON payload; on `json.JSONDecodeError` returns `validation_error`.
- Checks `is_streaming` flag; if `True` sends `stream_in_progress` and returns.
- Sets `is_streaming = True`, generates `request_id` UUID.
- Starts OTel CHAIN span with `request_id`, `model`, `consumer_id`.
- Runs existing validation gates: `_validate_vision`, `_validate_function_calling`,
  `_validate_structured_output` (reusing exact same functions from `main.py`).
- Forces `body["stream"] = True` and calls LiteLLM via `httpx.AsyncClient` with
  `stream=True`.
- Iterates SSE lines: strips `data: ` prefix, skips `[DONE]`, forwards each JSON
  chunk as `await websocket.send_text(chunk_json)`.
- On `[DONE]`: sends final chunk (already sent), then sends `{"type":"stream_complete"}`.
- On non-200 from LiteLLM: sends `model_error` or `rate_limit_error`.
- On exception mid-stream: sends `stream_error`.
- Always closes OTel span in `finally`.
- Calls `_write_audit(...)` with `event_type="ws_stream_request"`.
- Sets `is_streaming = False`.

**A4. Register in `main.py`**
- Import and wire the WS handler.
- Extend `_lifespan()` to initialise `app.state.ws_connections` and `app.state.ws_lock`.
- Add `WS_MAX_CONNECTIONS_PER_KEY` and `WS_IDLE_TIMEOUT_SECONDS` env reads.

### Task Group B — Kong: WebSocket route (`kong.yml`)

**B1. New service `litellm-ws`**
```yaml
- name: litellm-ws
  url: http://guardrails:8088
  connect_timeout: 5000
  read_timeout:    120000
  write_timeout:   120000
```

**B2. New route `ws-chat-completions`**
```yaml
routes:
  - name: ws-chat-completions
    paths:
      - /ws/v1/chat/completions
    protocols:
      - http
      - ws
    strip_path: false
    plugins:
      - name: key-auth
        config:
          key_names:
            - Authorization
          key_in_header: true
          hide_credentials: false   # guardrails needs consumer headers; Kong sets X-Consumer-Username
```

Note: `hide_credentials` set to `false` ensures Kong forwards consumer identity
headers (`X-Consumer-Username`) to guardrails for connection-limit tracking.
The API key itself is still not forwarded to LiteLLM (guardrails strips it before
the SSE call, consistent with the existing HTTP path).

### Task Group C — docker-compose.yml

**C1. Add env vars to `guardrails` service**
```yaml
WS_MAX_CONNECTIONS_PER_KEY: ${WS_MAX_CONNECTIONS_PER_KEY:-10}
WS_IDLE_TIMEOUT_SECONDS:    ${WS_IDLE_TIMEOUT_SECONDS:-300}
```

**C2. Add to `.env.example`**
```
WS_MAX_CONNECTIONS_PER_KEY=
WS_IDLE_TIMEOUT_SECONDS=
```

### Task Group D — Tests

**D1. Smoke test** (`tests/smoke/test_websocket_streaming.sh`)
- Test 1: Connect, send payload, verify JSON frames contain `chat.completion.chunk`, verify `stream_complete` received.
- Test 2: Send second request on same connection after `stream_complete`.
- Test 3: Unauthenticated connection → HTTP 401.
- Test 4: Malformed payload → `validation_error` JSON frame, connection stays open.
- Uses `wscat --execute` for non-interactive execution.

**D2. Contract tests** (`tests/contract/test_ws_streaming.py`)
- `test_connect_unauthenticated`: asserts 401 at upgrade.
- `test_single_stream`: verifies token delta schema, `finish_reason`, `stream_complete`.
- `test_persistent_connection`: two sequential requests on one connection.
- `test_stream_in_progress`: sends second message mid-stream, asserts `stream_in_progress` error, asserts first stream continues.
- `test_connection_limit`: opens 11 connections, asserts 11th is rejected with `connection_limit_exceeded`.
- `test_idle_timeout` (integration, skipped in unit): fast-path by setting `WS_IDLE_TIMEOUT_SECONDS=2`.

---

## Acceptance Criteria (falsifiable)

| ID | Command / Query | Expected |
|---|---|---|
| SC-001 | `wscat` send payload; measure ms from send to first `chat.completion.chunk` received | p95 ≤ 600 ms |
| SC-002 | Send 50 sequential payloads on one `wscat` session | 50 `stream_complete` messages, no reconnect |
| SC-004 | `curl http://localhost:3100/loki/api/v1/query?query={service="guardrails"}\|json\|event_type="ws_stream_request"` after Test 1 | Returns ≥1 log entry with no `input` or `output` fields |
| SC-005 | `wscat -c ws://localhost:8080/ws/v1/chat/completions` (no auth header) | `Error: Unexpected server response: 401` |
| SC-007 | `make stats` while 200 wscat sessions are active | `guardrails` row stays within its existing memory limit |
| SC-008 | Phoenix UI → Traces after Test 1 | 1 CHAIN span per request; span `input.value` absent |
| SC-009 | Open 11 connections (same key) | 11th rejected; first 10 unaffected |
| FR-013 | Send second payload while stream active in wscat | `{"type":"stream_in_progress",…}` received; first stream completes normally |

---

## Complexity Tracking

No constitution violations. No unusual abstractions introduced.

| Observation | Justification |
|---|---|
| `hide_credentials: false` on WS Kong route | Required so guardrails receives `X-Consumer-Username` for connection-limit enforcement. The API key is stripped before the LiteLLM call inside guardrails — same as the HTTP path. |
| SSE-to-WS bridge in guardrails (not a native WS-to-WS proxy) | LiteLLM v1.52.0 has no native WS endpoint. The bridge is the minimal correct approach within the locked tech stack. Revisit if LiteLLM adds WS in a future version. |
