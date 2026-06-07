# Contract: WebSocket Streaming Protocol

**Branch**: `021-websocket-streaming` | **Date**: 2026-06-07

This document is the authoritative protocol contract for the
`/ws/v1/chat/completions` endpoint. Callers that conform to this contract
require no changes when the underlying implementation changes.

---

## Endpoint

```
ws://<gateway-host>:8080/ws/v1/chat/completions
```

In local development: `ws://localhost:8080/ws/v1/chat/completions`

---

## Connection Establishment

**Required HTTP upgrade header**:

```
Authorization: <platform-api-key>
```

The API key is the same credential used for all other platform endpoints
(`/v1/chat/completions`, `/v1/models`, etc.).

**Rejection conditions** (handshake closes immediately):
- No `Authorization` header → HTTP 401
- Invalid or unknown API key → HTTP 401
- Caller already holds 10 open connections → HTTP 429 + JSON body `{"type": "connection_limit_exceeded", ...}`

**On success**: HTTP 101 Switching Protocols. The connection is immediately ready
to accept request messages.

---

## Client → Server: Request Message

Send one JSON text frame per chat completion request. The schema is identical to
the OpenAI `/v1/chat/completions` request body.

```json
{
  "model": "gpt-4o-mini",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is the capital of France?"}
  ],
  "temperature": 0.7,
  "max_tokens": 200
}
```

**Notes**:
- The `stream` field is ignored; the platform always streams.
- Any valid OpenAI chat completion parameter is accepted and forwarded.
- Do **not** send a second request while a stream is active (see error table below).

---

## Server → Client: Message Sequence

For every accepted request, the server sends this sequence of JSON text frames:

### 1. Token delta frames (zero or more)

One frame per token. OpenAI streaming chunk format.

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion.chunk",
  "model": "gpt-4o-mini",
  "choices": [
    {
      "delta": {"content": "Paris"},
      "index": 0,
      "finish_reason": null
    }
  ]
}
```

### 2. Final chunk (exactly one)

The last OpenAI chunk. `finish_reason` is set (`"stop"`, `"length"`, etc.).
`delta.content` may be empty or absent.

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion.chunk",
  "model": "gpt-4o-mini",
  "choices": [
    {
      "delta": {},
      "index": 0,
      "finish_reason": "stop"
    }
  ]
}
```

### 3. Stream-complete sentinel (exactly one)

Immediately follows the final chunk. Unambiguous end-of-stream signal.

```json
{"type": "stream_complete"}
```

After receiving `stream_complete`, the caller may send the next request on the
same connection.

---

## Server → Client: Error Messages

Error messages are JSON text frames. The connection **stays open** after all
errors except `auth_error` and `connection_closing`.

| `type` | Meaning | Connection |
|---|---|---|
| `validation_error` | Malformed payload or guardrail block | Stays open |
| `stream_in_progress` | New request sent while stream is active | Stays open |
| `rate_limit_error` | Upstream rate limit exceeded | Stays open |
| `model_error` | LiteLLM returned a non-200 before streaming began | Stays open |
| `stream_error` | Upstream error during an active stream | Stays open |
| `auth_error` | Credential expired while connection is live | Closed (1008) |
| `connection_closing` | Idle timeout exceeded | Closed (1001) |

**Error frame schema**:

```json
{
  "type": "stream_in_progress",
  "error": "stream_in_progress",
  "message": "A stream is already active. Wait for stream_complete before sending a new request.",
  "detail": {}
}
```

---

## Connection Lifetime

- **Idle timeout**: The server closes the connection with code 1001 and a
  `connection_closing` message after `WS_IDLE_TIMEOUT_SECONDS` (default 300 s)
  of no incoming request.
- **Caller close**: The caller may close the connection at any time (including
  mid-stream). The server cleans up all stream resources.
- **Sequential requests only**: One active stream at a time per connection.
  Send the next request only after receiving `stream_complete`.

---

## Connection Limits

- Maximum **10 simultaneous open connections** per API key (configurable by
  platform operators without a code change).
- A connection attempt that would exceed this limit is rejected at handshake
  time with HTTP 429.

---

## Example Session

```
Client                                    Server
  │                                          │
  │── WS Upgrade (Authorization: key) ──────►│
  │◄─ 101 Switching Protocols ───────────────│
  │                                          │
  │── {"model":"gpt-4o-mini","messages":[…]} ►│
  │◄─ {"object":"chat.completion.chunk",…}   │ (token 1)
  │◄─ {"object":"chat.completion.chunk",…}   │ (token 2)
  │◄─ {"object":"chat.completion.chunk",…}   │ (final, finish_reason="stop")
  │◄─ {"type":"stream_complete"}             │
  │                                          │
  │── {"model":"gpt-4o-mini","messages":[…]} ►│  (second request, no reconnect)
  │◄─ {"object":"chat.completion.chunk",…}   │ (token 1)
  │   …                                      │
  │◄─ {"type":"stream_complete"}             │
  │                                          │
  │── WS Close ─────────────────────────────►│
  │◄─ WS Close ──────────────────────────────│
```

---

## Guardrail Pipeline (transparent to callers)

Every request message traverses the full pipeline before LiteLLM is called:

1. **Kong** — key-auth, rate-limit, request-size check, W3C traceparent injection
2. **Guardrails** — vision gate, function-calling gate, structured-output gate, PII redaction
3. **LiteLLM** — model routing, fallback, caching (streaming responses are never cached)

A guardrail block returns a `validation_error` message over the WebSocket. The
connection stays open.
