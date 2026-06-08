# API Contract: Guardrails Bypass Flag

## Endpoint

`POST /v1/chat/completions` (existing endpoint — no new endpoint)

## Request Body Extension

The `guardrails` field is an optional boolean extension to the standard OpenAI chat completions request body.

```json
{
  "model": "claude-sonnet",
  "max_tokens": 1024,
  "messages": [{"role": "user", "content": "..."}],
  "stream": true,
  "guardrails": false
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `guardrails` | `boolean` | `true` | When `false`: skip all validation gates in the guardrails service. When `true` or absent: full validation. |

**Stripping**: The `guardrails` field is removed from the body before the request is forwarded to LiteLLM. LiteLLM and upstream model APIs never receive this field.

## Behaviour Matrix

| `guardrails` | `stream` | Behaviour |
|---|---|---|
| absent / `true` | any | All validation gates run. Streaming responses are buffered for post-inference scan. |
| `false` | `false` or absent | Direct buffered passthrough to LiteLLM. No validation. Audit log written. |
| `false` | `true` | True SSE streaming passthrough via `httpx client.stream()`. Chunks forwarded in real time. No validation. Audit log written. |

## Response (bypass path)

Identical to the standard chat completions response. No additional fields are added or removed by the bypass path.

**Streaming** (`guardrails: false, stream: true`):

```
HTTP/1.1 200 OK
Content-Type: text/event-stream

data: {"id":"chatcmpl-...","choices":[{"index":0,"delta":{"content":"Hello","role":"assistant"}}],...}

data: {"id":"chatcmpl-...","choices":[{"index":0,"delta":{"content":"!"}}],...}

data: {"id":"chatcmpl-...","choices":[{"finish_reason":"stop","index":0,"delta":{}}],...}

data: [DONE]
```

**Non-streaming** (`guardrails: false, stream: false`):

```json
{
  "id": "chatcmpl-...",
  "choices": [{"finish_reason": "stop", "index": 0, "message": {"content": "...", "role": "assistant"}}],
  "model": "claude-sonnet-4-5",
  "object": "chat.completion"
}
```

## Error Responses (bypass path)

Errors from LiteLLM are forwarded unchanged. The guardrails service does not wrap or reformat upstream errors on the bypass path.

**Example — model overloaded (forwarded as-is)**:

```
data: {"error":{"message":"litellm.InternalServerError: AnthropicException - Overloaded.","type":null,"param":null,"code":"500"}}
```

**Handling in clients**: Check for the `"error"` key before accessing `choices[0]`. If `"error"` is present, print the message and stop reading the stream.

## Authentication

Unchanged — Kong key-auth applies to all requests. The `guardrails` flag does not affect authentication. Pass the raw consumer key in the `Authorization` header (no `Bearer` prefix):

```
Authorization: smoke-test-key-dev
```
