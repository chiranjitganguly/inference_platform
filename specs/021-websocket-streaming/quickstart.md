# Quickstart: Testing WebSocket Streaming

**Branch**: `021-websocket-streaming` | **Date**: 2026-06-07

Test the `/ws/v1/chat/completions` WebSocket endpoint with `wscat`.

---

## Prerequisites

```bash
# Install wscat (requires Node.js)
npm install -g wscat

# Stack must be running (core profile)
make up-core
make seed-kong

# Export your API key
export SMOKE_API_KEY=your-api-key-here
```

---

## Test 1 — Basic Token Stream (SC-001, SC-003)

Connect and send one chat completion request. You should see token deltas
arrive in real time, followed by a `stream_complete` sentinel.

```bash
wscat \
  --connect "ws://localhost:8080/ws/v1/chat/completions" \
  --header "Authorization: ${SMOKE_API_KEY}"
```

After the connection opens (you see `Connected`), type and press Enter:

```json
{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Say hello in three words."}]}
```

**Expected output** (one JSON object per line, token by token):
```
< {"id":"chatcmpl-...","object":"chat.completion.chunk","model":"gpt-4o-mini","choices":[{"delta":{"content":"Hello"},"index":0,"finish_reason":null}]}
< {"id":"chatcmpl-...","object":"chat.completion.chunk","model":"gpt-4o-mini","choices":[{"delta":{"content":" there"},"index":0,"finish_reason":null}]}
< ...
< {"id":"chatcmpl-...","object":"chat.completion.chunk","model":"gpt-4o-mini","choices":[{"delta":{},"index":0,"finish_reason":"stop"}]}
< {"type":"stream_complete"}
```

---

## Test 2 — Persistent Connection (FR-004, SC-002)

Send a second request **on the same connection** without reconnecting.

After receiving `stream_complete` from Test 1, type:

```json
{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "What is 2 + 2?"}]}
```

**Expected**: A new token stream begins immediately. No reconnection occurs.
`wscat` stays connected throughout both requests.

---

## Test 3 — Stream-in-Progress Rejection (FR-013)

Send two requests in rapid succession (before the first stream completes).

Open two terminal windows. In window 1, connect:

```bash
wscat \
  --connect "ws://localhost:8080/ws/v1/chat/completions" \
  --header "Authorization: ${SMOKE_API_KEY}"
```

Send a long-running request:

```json
{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Write a 200-word story."}]}
```

While tokens are still arriving, immediately send a second payload:

```json
{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "What is 1 + 1?"}]}
```

**Expected**: A `stream_in_progress` error is returned. The first stream continues
uninterrupted. The connection stays open.

```json
{"type":"stream_in_progress","error":"stream_in_progress","message":"A stream is already active. Wait for stream_complete before sending a new request.","detail":{}}
```

---

## Test 4 — Unauthenticated Rejection (FR-005, SC-005)

```bash
wscat --connect "ws://localhost:8080/ws/v1/chat/completions"
```

**Expected**: Connection is refused with HTTP 401. `wscat` exits immediately.

---

## Test 5 — Connection Limit (FR-008, SC-009)

Open 11 concurrent connections using the same API key.

```bash
# Run in a loop (each wscat stays open due to --wait)
for i in $(seq 1 11); do
  wscat \
    --connect "ws://localhost:8080/ws/v1/chat/completions" \
    --header "Authorization: ${SMOKE_API_KEY}" \
    --no-check &
done
```

**Expected**: The first 10 connections succeed. The 11th receives HTTP 429 at
handshake time and is rejected before the WS upgrade completes.

---

## Smoke Test (automated)

```bash
make smoke
```

The smoke test script at `tests/smoke/test_websocket_streaming.sh` covers
Tests 1–4 above non-interactively using `wscat --execute`.

---

## Verify Telemetry

After running Test 1, check Phoenix Arize for a CHAIN span:

```bash
# Phoenix UI (when running with obs profile)
open http://localhost:6006
```

The span should show:
- Kind: CHAIN
- Attributes: `request.id`, `model`, `consumer.id`
- No `input.value` or `output.value` attributes (prompt content never stored)
- Duration: from first byte received to `[DONE]` from LiteLLM

Check Loki for the audit entry:

```bash
# Loki query in Grafana
open http://localhost:3000
# Query: {service="guardrails"} | json | event_type = "ws_stream_request"
```
