# Data Model: Streaming Chat Completions

**Feature**: `006-streaming-sse` | **Date**: 2026-05-28

## Entities

### StreamingRequest

Identical to `ChatRequest` (feature 005) with `stream: true`. No new fields.

| Field | Type | Required | Constraints |
|---|---|---|---|
| `model` | string | yes | Must match a name in model_list; must be a chat model (not embedding) |
| `messages` | array[Message] | yes | minimum 1 element |
| `stream` | boolean | yes | `true` to activate streaming |
| `metadata` | object[Metadata] | no | Forwarded to Phoenix and Langfuse identically to non-streaming |

**Cache**: Requests with `stream: true` MUST bypass the Redis response cache (read and write).

---

### StreamChunk

A single Server-Sent Events event delivered to the caller. Each chunk is a complete, independently parseable JSON object.

SSE wire format:
```
data: <StreamChunkJSON>\n\n
```

| Field | Type | Constraints | Notes |
|---|---|---|---|
| `id` | string | non-empty | Same value across all chunks in one response |
| `object` | string | `"chat.completion.chunk"` | Fixed value |
| `model` | string | echoes requested model | Same value across all chunks |
| `choices` | array[StreamChoice] | exactly 1 element | Non-streaming path has 1 element too |

---

### StreamChoice

One element of `StreamChunk.choices`.

| Field | Type | Constraints | Notes |
|---|---|---|---|
| `index` | integer | `0` | Fixed for non-streaming |
| `delta` | object[Delta] | — | Content carrier; may be empty object on final chunk |
| `finish_reason` | string or null | `null` for all intermediate chunks; `"stop"` \| `"length"` \| `"content_filter"` on final chunk | Never `null` AND final simultaneously |

#### Delta

| Field | Type | Present when | Notes |
|---|---|---|---|
| `role` | string | First chunk only | `"assistant"` |
| `content` | string | All content chunks | May be empty string `""` on first chunk if role-only |

**Independently parseable constraint**: Every `StreamChunk` must be valid JSON on its own line. No chunk may depend on state from a prior chunk for JSON validity.

---

### StreamSentinel

The termination signal. Not a JSON object — a literal string.

```
data: [DONE]\n\n
```

- MUST be the last `data:` line
- Preceded by the final `StreamChunk` with `finish_reason` set
- No JSON parsing required; clients should check `data == "[DONE]"` string equality

---

### StreamErrorEvent

Emitted by LiteLLM when the upstream provider errors after HTTP 200 headers are sent. A parseable JSON SSE event, not the `[DONE]` sentinel.

```
data: {"error": {"message": "...", "type": "upstream_error", "code": <int>}}\n\n
```

| Field | Type | Notes |
|---|---|---|
| `error.message` | string | Human-readable upstream error |
| `error.type` | string | `"upstream_error"` |
| `error.code` | integer | HTTP-equivalent status from upstream |

After this event, the connection closes. No `[DONE]` is sent.

---

### PhoenixStreamSpan

The single Phoenix Arize span emitted per streaming request. Not a wire entity — internal observability record.

| Attribute | OpenInference Key | Value | Timing |
|---|---|---|---|
| Model name | `llm.model_name` | requested model | At span open |
| Prompt tokens | `llm.token_count.prompt` | from final chunk `usage` | At span close |
| Completion tokens | `llm.token_count.completion` | from final chunk `usage` | At span close |
| Stream flag | `metadata.stream` | `true` | At span open |
| Team tag | `metadata.team` | from request metadata | At span open |
| Request ID | `metadata.request_id` | from request metadata | At span open |

**Span lifecycle**: Open at request start → close when generator exhausted (`[DONE]`) or caller disconnects.

---

## State Transitions

### Streaming Response Lifecycle

```
POST received (stream: true)
       │
       ├─ Cache bypass check → cache skipped (stream=true not in supported_call_types)
       │
       ├─ Model validation → unknown model? → HTTP 400 (before any headers sent)
       │
       ├─ Auth check at Kong → invalid key? → HTTP 401 (before any headers sent)
       │
       ├─ HTTP 200 + Content-Type: text/event-stream headers sent
       │
       ├─ Phoenix span OPEN
       │
       ├─ SSE chunks forwarded: data: {StreamChunk}\n\n  (repeated)
       │         │
       │         └─ upstream error mid-stream?
       │                   └─ data: {StreamErrorEvent}\n\n → connection close
       │                             Phoenix span CLOSE (partial token counts)
       │
       ├─ data: {final StreamChunk with finish_reason set}\n\n
       │
       ├─ data: [DONE]\n\n
       │
       └─ connection close
             │
             ├─ Phoenix span CLOSE (full token counts emitted)
             └─ Langfuse trace CREATED (async, via langfuse callback)
```

### Cache Decision

```
request received
       │
       ├─ stream: true? → BYPASS CACHE (no read, no write)
       │
       └─ stream: false/absent? → normal cache flow (read-then-write)
```
