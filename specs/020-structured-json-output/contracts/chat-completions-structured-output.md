# Contract: Structured JSON Output — POST /v1/chat/completions

**Feature**: 020-structured-json-output | **Date**: 2026-06-07

This contract extends the existing `/v1/chat/completions` endpoint. All existing request/response fields remain valid. Only the additions and error cases specific to structured output mode are documented here.

---

## Activation

Structured output mode is activated by including a `response_format` object in the request body:

```json
{
  "type": "json_schema",
  "name": "<schema-identifier>",
  "strict": true,
  "schema": { ... }
}
```

| Field | Required | Type | Notes |
|---|---|---|---|
| `type` | Yes | `"json_schema"` | Literal. Any other value uses the existing code path. |
| `name` | Yes | non-empty string | Schema identifier. Appears in Phoenix span and error responses. |
| `strict` | No | boolean | Defaults to `true`. No lenient mode — `false` is treated as `true`. |
| `schema` | Yes | object | JSON Schema (draft-07 or later). Must be self-contained — no external `$ref`. |

---

## Request Examples

### Success path — native model

```json
POST /v1/chat/completions
Authorization: Bearer <api-key>
Content-Type: application/json

{
  "model": "gpt-4o-mini",
  "messages": [
    {"role": "user", "content": "Extract: Invoice #INV-001 for $99.99"}
  ],
  "response_format": {
    "type": "json_schema",
    "name": "invoice_schema",
    "strict": true,
    "schema": {
      "type": "object",
      "properties": {
        "invoice_number": {"type": "string"},
        "total":          {"type": "number"}
      },
      "required": ["invoice_number", "total"],
      "additionalProperties": false
    }
  }
}
```

### Success path — prompt-based model

```json
{
  "model": "gemini-flash",
  "messages": [{"role": "user", "content": "List three fruits."}],
  "response_format": {
    "type": "json_schema",
    "name": "fruit_list_schema",
    "strict": true,
    "schema": {
      "type": "object",
      "properties": {
        "fruits": {"type": "array", "items": {"type": "string"}}
      },
      "required": ["fruits"],
      "additionalProperties": false
    }
  }
}
```

---

## Response — Success (HTTP 200)

The response is a standard OpenAI chat completion object. The `choices[0].message.content` field is always a JSON **string** — a string value that, when parsed, yields an object conforming to the caller-provided schema.

```json
HTTP/1.1 200 OK
X-Request-ID: <uuid>
X-Platform: inference-platform
X-API-Version: 1

{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "model": "gpt-4o-mini",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "{\"invoice_number\": \"INV-001\", \"total\": 99.99}"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": { "prompt_tokens": 42, "completion_tokens": 18, "total_tokens": 60 }
}
```

**Key invariant**: `json.loads(choices[0].message.content)` always succeeds and `jsonschema.validate(result, schema)` always passes for a 200 response.

---

## Response — Error Cases

### 400 — Streaming not supported with structured output

```json
HTTP/1.1 400 Bad Request

{
  "error": {
    "message": "Streaming is not supported when response_format.type is json_schema.",
    "type": "structured_output_streaming_not_supported",
    "code": "structured_output_streaming_not_supported"
  }
}
```

**Trigger**: Request includes both `"stream": true` and `response_format.type: "json_schema"`.

---

### 400 — Invalid structured output request (missing name)

```json
HTTP/1.1 400 Bad Request

{
  "error": {
    "message": "response_format.name is required and must be a non-empty string.",
    "type": "invalid_structured_output_request",
    "code": "invalid_structured_output_request"
  }
}
```

**Trigger**: `response_format.name` is absent, `null`, or an empty string.

---

### 400 — Invalid structured output request (missing schema)

```json
HTTP/1.1 400 Bad Request

{
  "error": {
    "message": "response_format.schema is required and must be a JSON object.",
    "type": "invalid_structured_output_request",
    "code": "invalid_structured_output_request"
  }
}
```

**Trigger**: `response_format.schema` is absent or not a JSON object.

---

### 400 — Model does not support structured output

```json
HTTP/1.1 400 Bad Request

{
  "error": {
    "message": "Model 'command-r-plus' does not support structured output (json_schema mode). Supported models: gpt-4o, gpt-4o-mini, gpt-4.1, o4-mini, claude-sonnet, claude-haiku, gemini-pro, gemini-flash.",
    "type": "structured_output_model_required",
    "code": "structured_output_model_required"
  }
}
```

**Trigger**: The requested model does not have `json_schema` in its `capabilities` in `config.yaml`.

---

### 422 — Invalid JSON schema (meta-schema validation failure)

```json
HTTP/1.1 422 Unprocessable Entity

{
  "error": {
    "message": "The provided schema is not a valid JSON Schema: 'bogus_type' is not valid under any of the given schemas.",
    "type": "invalid_json_schema",
    "code": "invalid_json_schema"
  }
}
```

**Trigger**: The `response_format.schema` object fails draft-07 meta-schema validation. No inference call is made.

---

### 422 — Schema conformance failure (retries exhausted)

```json
HTTP/1.1 422 Unprocessable Entity

{
  "error": {
    "message": "Model response did not conform to the provided JSON schema after 3 attempt(s).",
    "type": "schema_conformance_failure",
    "code": "schema_conformance_failure"
  },
  "retry_count": 3,
  "schema_name": "invoice_schema"
}
```

**Trigger**: `STRUCTURED_OUTPUT_MAX_RETRIES` (default 3) upstream calls all returned non-conforming JSON. The `retry_count` field equals the number of retries attempted (not including the initial attempt). The `schema_name` echoes the caller's `response_format.name`.

---

## Constraints

| Constraint | Value |
|---|---|
| `stream: true` | Not supported with structured output; returns 400 |
| External `$ref` in schema | Not resolved; treat as unknown schema keyword → 422 |
| Max retries | `STRUCTURED_OUTPUT_MAX_RETRIES` env var, default `3` |
| Schema size | Subject to existing Kong request body size limit (feature 015) |
| `strict: false` | Treated as `strict: true`; no lenient mode |
