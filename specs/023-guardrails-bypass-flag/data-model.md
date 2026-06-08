# Data Model: Guardrails Bypass Flag

## Code Entities

### `_guardrails_on: bool`

Local variable in `proxy()`. Set by parsing the `guardrails` key from the request body. Defaults to `True` on any parse failure or when the key is absent.

| Value | Source | Effect |
|---|---|---|
| `True` | Default / `guardrails` absent / `guardrails: true` | All validation gates run |
| `False` | `guardrails: false` | All validation gates skipped; bypass path taken |

### `_streaming_passthrough(request, body, headers, path) → StreamingResponse`

| Parameter | Type | Description |
|---|---|---|
| `request` | `fastapi.Request` | Original request — used for method and query params |
| `body` | `bytes` | Request body with `guardrails` key already stripped |
| `headers` | `dict[str, str]` | Forwarding headers (host/content-length/transfer-encoding removed) |
| `path` | `str` | URL path, e.g. `v1/chat/completions` |

Internal state:

| Variable | Type | Lifecycle |
|---|---|---|
| `_client` | `httpx.AsyncClient` | Opened via `__aenter__`; closed in `_gen()` finally block |
| `_stream_ctx` | `httpx._client.AsyncStreamContextManager` | Opened via `__aenter__`; closed in `_gen()` finally block |
| `_upstream` | `httpx.Response` | Headers available immediately after `__aenter__`; body streamed via `aiter_bytes()` |

### `_gen()` (inner async generator)

Yields `bytes` chunks from `_upstream.aiter_bytes()`. The `finally` block calls `__aexit__` on both `_stream_ctx` and `_client` in reverse order, ensuring cleanup whether the generator is exhausted normally or cancelled (client disconnect).

## Request Body Contract

```json
{
  "model": "claude-sonnet",
  "max_tokens": 1024,
  "messages": [{"role": "user", "content": "..."}],
  "stream": true,
  "guardrails": false
}
```

`guardrails` is stripped before forwarding. Body forwarded to LiteLLM:

```json
{
  "model": "claude-sonnet",
  "max_tokens": 1024,
  "messages": [{"role": "user", "content": "..."}],
  "stream": true
}
```

## Request Flow

### Bypass path (`guardrails: false, stream: true`)

```
Client
  → Kong :8080          (key-auth, rate-limit — unchanged)
  → Guardrails :8088
      parse body → pop guardrails flag → strip from body
      open httpx client + stream context
      capture upstream.status_code from response headers
      write audit log entry
      return StreamingResponse(_gen())
        _gen() → aiter_bytes() → yield chunks → finally: __aexit__ both contexts
  → LiteLLM :4000       (receives clean body, no guardrails key)
  → Cloud LLM API
```

SSE chunks flow back through `_gen()` → `StreamingResponse` → Kong → client in real time.

### Bypass path (`guardrails: false, stream: false`)

```
Client
  → Kong :8080
  → Guardrails :8088
      parse body → pop guardrails flag
      async with httpx.AsyncClient() → client.request() → buffered response
      write audit log entry
      return Response(content=_up.content, ...)
  → LiteLLM :4000
  → Cloud LLM API
```

### Standard path (`guardrails: true` or absent)

Unchanged. Vision → function-calling → structured output validation → LiteLLM → post-proxy checks → caller.

## Audit Log Entry (bypass)

Same schema as existing audit entries. Fields that differ on bypass path:

| Field | Bypass value | Notes |
|---|---|---|
| `pii_entity_count` | `0` | Scanning skipped |
| `scanner_blocked` | `false` | Scanning skipped |
| `image_part_count` | `0` | Vision gate skipped |
| `tool_count` | `0` | FC gate skipped |
| `schema_name` | `""` | SO gate skipped |
