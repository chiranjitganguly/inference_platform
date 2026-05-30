# Gateway API Contract

**Feature**: 012-kong-api-gateway-auth
**Date**: 2026-05-30
**Base URL**: `http://localhost:8080` (development) / `https://<platform-host>` (production)

This document defines the external interface of the Kong gateway after feature 012 is applied. All client traffic enters exclusively through port 8080. No internal service port is reachable externally.

---

## Authentication

All endpoints except `/health` require an API key in the `Authorization` request header.

```
Authorization: <raw-api-key>
```

- **No `Bearer ` prefix.** The raw key is the full header value.
- Keys are provisioned by the platform operator and delivered out-of-band.
- An absent or unrecognised key returns HTTP 401.

---

## Response Headers (all responses)

Kong attaches the following headers to every response, regardless of endpoint or auth status:

| Header | Value | Notes |
|---|---|---|
| `X-Request-ID` | UUID v4 | Unique per request; echoed to client for correlation |
| `X-Platform` | `inference-platform` | Static platform identifier |
| `X-API-Version` | `1` | Current stable API version |

---

## Endpoints

### GET /health

Liveness probe. **No authentication required.**

**Request**
```
GET /health HTTP/1.1
Host: localhost:8080
```

**Response — healthy**
```
HTTP/1.1 200 OK
Content-Type: application/json

{"status": "ok"}
```

**Response — unhealthy**
```
HTTP/1.1 503 Service Unavailable
```

---

### GET /v1/models

Returns the model catalogue. **Authentication required.**

**Request**
```
GET /v1/models HTTP/1.1
Host: localhost:8080
Authorization: <api-key>
```

**Response — authenticated**
```
HTTP/1.1 200 OK
Content-Type: application/json

{
  "object": "list",
  "data": [
    {"id": "gpt-4o", "object": "model", ...},
    ...
  ]
}
```

**Response — unauthenticated**
```
HTTP/1.1 401 Unauthorized
Content-Type: application/json

{
  "error": "unauthorized",
  "message": "No API key found in request",
  "detail": {}
}
```

---

### POST /v1/chat/completions

Inference endpoint. **Authentication required.**

**Request**
```
POST /v1/chat/completions HTTP/1.1
Host: localhost:8080
Authorization: <api-key>
Content-Type: application/json

{
  "model": "gpt-4o",
  "messages": [{"role": "user", "content": "Hello"}]
}
```

**Response — authenticated**
```
HTTP/1.1 200 OK
Content-Type: application/json

{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "model": "gpt-4o",
  "choices": [...]
}
```

**Response — unauthenticated**
```
HTTP/1.1 401 Unauthorized
```

---

### POST /v1/embeddings

Embedding endpoint. **Authentication required.** 120 s read timeout.

**Request**
```
POST /v1/embeddings HTTP/1.1
Host: localhost:8080
Authorization: <api-key>
Content-Type: application/json

{
  "model": "text-embedding-3-small",
  "input": ["text to embed"]
}
```

**Response — authenticated**
```
HTTP/1.1 200 OK
Content-Type: application/json

{
  "object": "list",
  "data": [{"object": "embedding", "embedding": [...], "index": 0}],
  "model": "text-embedding-3-small",
  "usage": {...}
}
```

---

### GET /v1/spend

Spend report. **Authentication required.**

**Request**
```
GET /v1/spend HTTP/1.1
Host: localhost:8080
Authorization: <api-key>
```

**Response**
```
HTTP/1.1 200 OK
Content-Type: application/json

{
  "spend": 0.0042,
  "currency": "USD",
  "period": "2026-05"
}
```

---

### POST /v1/key, GET /v1/key, DELETE /v1/key

LiteLLM virtual key management. **Authentication required** (master key scoped).

These endpoints are proxied to LiteLLM's internal key management API. Authentication at the Kong gateway layer is enforced; LiteLLM additionally validates that the presenting key is a master key.

---

### GET /v2/*, POST /v2/*

Future breaking-change surface. **Authentication required.** Currently forwarded to the same LiteLLM upstream as `/v1`. LiteLLM returns 404 for any `/v2` path until a `/v2` implementation is shipped.

The `Deprecation` response header will be set when a `/v1` endpoint is deprecated in favour of `/v2`.

---

## Error Response Schema

All Kong-originated error responses (401, 403, 429, etc.) use this structure:

```json
{
  "error": "<machine_readable_code>",
  "message": "<human readable description>",
  "detail": {}
}
```

LiteLLM upstream errors (400, 503, etc.) pass through in their original format, which is OpenAI-compatible.

---

## Ports

| Port | Accessible from | Purpose |
|---|---|---|
| 8080 | External clients | Kong proxy — the only public port |
| 8001 | localhost only | Kong Admin API — operator use only |
| 4000 | Internal Docker network only | LiteLLM — not externally reachable |
| all others | Internal Docker network only | Redis, Postgres, Prometheus, etc. |

---

## Acceptance Verification Commands

```bash
# Health — no auth, expect 200
curl -sf http://localhost:8080/health

# Models — valid key, expect 200
curl -s http://localhost:8080/v1/models \
  -H "Authorization: ${SMOKE_API_KEY}" | jq .

# Models — no key, expect 401
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/v1/models

# Chat — valid key, expect 200
curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}]}' | jq .

# Chat — no key, expect 401
curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}]}'

# Direct internal port — must fail (connection refused)
curl -sf http://localhost:4000/v1/models && echo "FAIL: internal port exposed" || echo "PASS: internal port not reachable"
```
