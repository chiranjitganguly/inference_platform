# API Contract: POST /v1/embeddings

**Version**: v1 | **Feature**: 011-embeddings-endpoint | **Date**: 2026-05-30

## Endpoint

```
POST http://localhost:8080/v1/embeddings
```

All traffic enters via Kong proxy on port 8080. Authentication is required on every request.

---

## Authentication

```
Authorization: Bearer <virtual-key>
```

Same virtual key mechanism as `/v1/chat/completions`. Missing or invalid key returns `401`.

---

## Request

**Content-Type**: `application/json`

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `model` | string | **yes** | `text-embedding-3-small` or `text-embedding-3-large` |
| `input` | string or string[] | **yes** | Text to embed. Single string or array of strings. Max 8191 tokens per string. |
| `encoding_format` | string | no | `"float"` (default). Omitting is equivalent to passing `"float"`. |

### Example — single input

```json
{
  "model": "text-embedding-3-small",
  "input": "The quick brown fox"
}
```

### Example — batch input

```json
{
  "model": "text-embedding-3-large",
  "input": [
    "First document to embed",
    "Second document to embed",
    "Third document to embed"
  ],
  "encoding_format": "float"
}
```

---

## Responses

### 200 OK — Success

```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "index": 0,
      "embedding": [-0.006929283495992422, -0.005336422473192215, "..."]
    }
  ],
  "model": "text-embedding-3-small",
  "usage": {
    "prompt_tokens": 5,
    "total_tokens": 5
  }
}
```

**Embedding array length**:
- `text-embedding-3-small` → exactly **1536** floats
- `text-embedding-3-large` → exactly **3072** floats

**Batch response**: `data` array contains one object per input string, with `index` matching the input position. Order is preserved.

---

### 400 Bad Request — Non-embedding model

```json
{
  "error": "model_type_mismatch",
  "message": "Model 'gpt-4o' is not an embedding model. Supported: text-embedding-3-small, text-embedding-3-large.",
  "detail": {
    "model": "gpt-4o",
    "expected_type": "embedding"
  }
}
```

**Triggers**: Any chat model name (`gpt-4o`, `claude-sonnet`, etc.) or unrecognised model name submitted to this endpoint.

---

### 400 Bad Request — Missing model field

```json
{
  "error": "missing_required_field",
  "message": "Field 'model' is required.",
  "detail": { "field": "model" }
}
```

---

### 400 Bad Request — Token limit exceeded

```json
{
  "error": "context_window_exceeded",
  "message": "Input exceeds the model's maximum token limit of 8191.",
  "detail": {
    "model": "text-embedding-3-small",
    "limit": 8191
  }
}
```

---

### 401 Unauthorised

```json
{
  "error": "unauthorized",
  "message": "Missing or invalid API key.",
  "detail": {}
}
```

---

### 429 Rate Limited

```json
{
  "error": "rate_limit_exceeded",
  "message": "Rate limit exceeded. Retry after the duration specified in Retry-After header.",
  "detail": {}
}
```

---

## Response Headers (attached by Kong on every response)

| Header | Value |
|---|---|
| `X-Request-ID` | UUID identifying this request end-to-end |
| `X-Platform` | `inference-platform` |
| `X-API-Version` | `1` |

---

## Behaviour Guarantees

| Guarantee | Detail |
|---|---|
| **No caching** | Every embedding request reaches the upstream provider. Identical requests sent twice will each produce an upstream API call. |
| **No Phoenix/Langfuse traces** | Embedding calls do not appear in Phoenix Arize or Langfuse when the obs profile is active. |
| **Prometheus metrics** | Token usage and request counts are emitted to Prometheus regardless of obs profile state. |
| **Model type enforcement** | Non-embedding models are rejected before any upstream call is made. |
| **Batch ordering** | Response `data[i]` always corresponds to `input[i]`. |
| **BYOK** | The underlying OpenAI API key is never returned in any response. |
