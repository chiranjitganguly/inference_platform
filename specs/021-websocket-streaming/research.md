# Research: WebSocket Streaming

**Branch**: `021-websocket-streaming` | **Date**: 2026-06-07

---

## Decision 1 — Where the WebSocket endpoint lives

**Decision**: Add `@app.websocket("/ws/v1/chat/completions")` to the **existing guardrails service** (FastAPI). No new service.

**Rationale**: The constitution requires every request to traverse Kong → Guardrails → LiteLLM. Adding WS support inside guardrails keeps the chain intact. FastAPI (Starlette) has first-class WebSocket support via the same `uvicorn[standard]` already in `requirements.txt`; `websockets` is a transitive install. No new Docker service means no new memory budget hit.

**Alternatives considered**:
- Dedicated `ws-gateway` service: rejected — adds a service to the chain and increases memory footprint on the 8 GB Mac.
- Route directly Kong → LiteLLM WS: rejected — violates the constitution (bypasses Guardrails entirely).
- Route Kong → Guardrails HTTP (SSE only, no WS): rejected — does not meet the persistent bidirectional connection requirement.

---

## Decision 2 — How the guardrails WS handler calls LiteLLM

**Decision**: Bridge each incoming WS message to an **HTTP SSE call** on LiteLLM (`POST http://litellm:4000/v1/chat/completions` with `stream: true`). Each SSE `data: {…}` line is forwarded as a discrete WebSocket JSON message. The `data: [DONE]` sentinel triggers a final empty delta with `finish_reason` set.

**Rationale**: LiteLLM v1.52.0 has no native WebSocket endpoint at `/ws/v1/chat/completions`. The SSE streaming path (`text/event-stream`) is already implemented and battle-tested in the guardrails service (`_stream_bytes`). `httpx.AsyncClient` with `stream=True` handles the SSE response. This avoids any LiteLLM image fork or version bump.

**Alternatives considered**:
- Fork LiteLLM to add a WS endpoint: rejected — requires a version bump, ADR, and higher blast radius.
- Use `aiohttp` SSE client: rejected — `httpx` is already the platform-standard async HTTP client.

---

## Decision 3 — Kong WebSocket passthrough configuration

**Decision**: Add a new Kong service (`litellm-ws`) targeting `http://guardrails:8088` with `read_timeout: 120000` and `write_timeout: 120000`. Route path: `/ws/v1/chat/completions` with protocols `["http", "ws"]` (Kong 3.6 supports WS upgrade passthrough on the same `http` protocol entry when the client sends an `Upgrade: websocket` header). Apply the existing `key-auth` plugin (same `Authorization` header as all other routes).

**Rationale**: Kong 3.6 transparently proxies WebSocket upgrades through a service defined with `http` protocol as long as the upstream also accepts the upgrade. Setting `read_timeout` and `write_timeout` to 120 000 ms prevents Kong from timing out long-lived streaming connections. `connect_timeout` at 5 000 ms is sufficient (same as health-check service). `key-auth` applies to the HTTP upgrade request, so authentication happens before the WS handshake completes — Kong's existing consumer pipeline works unchanged.

**Alternatives considered**:
- `wss` protocol route: deferred — TLS termination at Kong requires cert config out of scope for this feature.
- Separate Kong port for WS: rejected — all client traffic must enter on port 8080 (constitution §2.1).

---

## Decision 4 — Consumer identity and connection-limit tracking

**Decision**: Kong's `key-auth` plugin sets `X-Consumer-Username` on every authenticated request (even WebSocket upgrades, since auth runs on the HTTP upgrade before the handshake completes). The guardrails WS handler reads this header to identify the caller. Connection counts are tracked in **`app.state.ws_connections: dict[str, int]`** (consumer → count), guarded by an `asyncio.Lock` to make increment-and-check atomic within the single-process guardrails container.

**Rationale**: `app.state` is sufficient for a single-container deployment (the `core` profile). No new Redis dependency needed for v1. The default limit of 10 is read from the `WS_MAX_CONNECTIONS_PER_KEY` environment variable (default `10`); no code change required to adjust it.

**Alternatives considered**:
- Redis INCR/DECR: preferred for multi-instance deployments; deferred to a later feature when horizontal scaling of guardrails is planned.
- Count in Kong via a rate-limit plugin scoped to "connections": Kong rate-limit counts requests, not open connections; insufficient.

---

## Decision 5 — In-progress stream enforcement (FR-013)

**Decision**: A per-connection `is_streaming: bool` flag local to the WS handler coroutine. When `True`, any new payload triggers an immediate JSON error `{"type": "stream_in_progress", "error": "stream_in_progress", "message": "A stream is already active. Wait for stream_complete before sending a new request."}` without closing the connection.

**Rationale**: Simplest correct implementation for sequential (non-multiplexed) v1. The flag is coroutine-local — no shared state needed.

---

## Decision 6 — OpenTelemetry tracing per stream request

**Decision**: Each WS message (one stream request) opens one **CHAIN-kind OTel span** in the guardrails service. The span carries: `request.id`, `model`, `consumer.id`, `ws.connection_id`. The span closes when LiteLLM sends `[DONE]` or when a stream error is received. The connection lifetime is not a span.

**Rationale**: Matches the clarified spec requirement (Phoenix single span per stream, not per connection). Uses the OpenTelemetry SDK already imported by the guardrails service. Aligns with the platform's OpenInference CHAIN span kind used throughout guardrails.

**Alternatives considered**:
- One span per connection (wrapping all requests): rejected by the spec — Phoenix must show per-request quality signals (TTFT, token count, model).

---

## Decision 7 — Idle connection timeout

**Decision**: `asyncio.wait_for(websocket.receive_text(), timeout=WS_IDLE_TIMEOUT_SECONDS)` where `WS_IDLE_TIMEOUT_SECONDS` defaults to `300` (5 minutes, configurable via env var). On `asyncio.TimeoutError`, send `{"type": "connection_closing", "message": "Idle timeout exceeded"}` then call `websocket.close(1001)`.

**Rationale**: 5-minute default matches the spec assumption. `asyncio.wait_for` is the simplest way to implement idle timeouts in an async WS handler without background tasks.

---

## Decision 8 — Message wire format

**Decision**: 
- **Token deltas** (server → client): OpenAI streaming chunk JSON exactly as returned by LiteLLM (`{"id": "...", "object": "chat.completion.chunk", "model": "...", "choices": [{"delta": {"content": "..."}, "index": 0, "finish_reason": null}]}`).
- **Stream complete**: The final LiteLLM SSE chunk (which has `finish_reason` set) is forwarded as-is; the guardrails handler then sends one additional `{"type": "stream_complete"}` sentinel for callers that want an unambiguous signal.
- **Errors**: `{"type": "<error_code>", "error": "<error_code>", "message": "<human description>", "detail": {}}` — mirrors the platform's existing HTTP error schema with a `type` field added for WS disambiguation.
- **Client → server**: Any OpenAI `/v1/chat/completions` JSON body; the handler force-sets `stream: true` before forwarding to LiteLLM.

**Rationale**: Keeping token deltas in native OpenAI format means any OpenAI-compatible streaming client can parse them without changes. Adding `type: stream_complete` as a separate final message is a low-cost disambiguation for callers that don't parse `finish_reason`.
