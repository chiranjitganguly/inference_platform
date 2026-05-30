# Data Model: Embeddings Endpoint (011)

**Branch**: `011-embeddings-endpoint` | **Date**: 2026-05-30

## New Storage

None. This feature introduces no new tables, collections, or Redis keys.

## Affected Existing Storage

### `litellm` PostgreSQL database — `litellm_spendlogs`

Embedding calls are automatically written to LiteLLM's existing spend log table by the LiteLLM proxy. No schema changes required. The `model` column will contain the embedding model name (`text-embedding-3-small` or `text-embedding-3-large`).

Relevant columns (subset):
| Column | Type | Value for embeddings |
|---|---|---|
| `model` | varchar | `text-embedding-3-small` \| `text-embedding-3-large` |
| `call_type` | varchar | `embedding` |
| `prompt_tokens` | int | token count of input text(s) |
| `completion_tokens` | int | `0` (embeddings produce no completion tokens) |
| `spend` | float | cost at OpenAI pricing for the model |
| `request_id` | varchar | `X-Request-ID` from Kong correlation header |
| `api_key` | varchar | hashed virtual key (never plaintext) |

### Redis cache — no impact

The cache change (removing `aembedding`/`embedding` from `supported_call_types`) means embedding requests never write to or read from Redis. The existing `llm_cache` namespace is unaffected.

---

## Wire Schemas

### Request (POST /v1/embeddings)

```json
{
  "model": "text-embedding-3-small",
  "input": "string or [\"array\", \"of\", \"strings\"]",
  "encoding_format": "float"
}
```

| Field | Type | Required | Constraint |
|---|---|---|---|
| `model` | string | yes | Must be `text-embedding-3-small` or `text-embedding-3-large` |
| `input` | string \| string[] | yes | Non-empty; max 8191 tokens per input string |
| `encoding_format` | string | no | Supported value: `float` (default); `base64` is undocumented passthrough |

### Response (200 OK)

```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "index": 0,
      "embedding": [0.0023, -0.0098, 0.0412, "... N floats"]
    }
  ],
  "model": "text-embedding-3-small",
  "usage": {
    "prompt_tokens": 8,
    "total_tokens": 8
  }
}
```

| Field | Type | Constraint |
|---|---|---|
| `data[i].embedding` | float[] | Length = 1536 for `text-embedding-3-small`; 3072 for `text-embedding-3-large` |
| `data[i].index` | int | Matches position of input string in request array |
| `usage.prompt_tokens` | int | > 0 |
| `usage.total_tokens` | int | Equals `prompt_tokens` (no completion tokens for embeddings) |

### Error Response (400)

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

---

## Model Catalogue Entries (no change required)

Both models are already declared in `services/litellm/config.yaml`:

| model_name | dimensions | type | context_window | api_key env var |
|---|---|---|---|---|
| `text-embedding-3-small` | 1536 | embedding | 8191 | `OPENAI_API_KEY` |
| `text-embedding-3-large` | 3072 | embedding | 8191 | `OPENAI_API_KEY` |
