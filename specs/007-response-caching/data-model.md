# Data Model: Response Caching Layer

**Feature**: `007-response-caching` | **Date**: 2026-05-28

---

## Entities

### CacheKey

A deterministic identifier computed at request time. Used for both cache lookup (read) and cache write.

| Field | Type | Constraints |
|---|---|---|
| `model_name` | string | Required; must match a registered LiteLLM model alias |
| `messages` | array of Message | Ordered; byte-exact comparison; minimum 1 element |
| `temperature` | float | Range 0.0–2.0; default 1.0 when omitted from request |
| `hash` | string (SHA-256 hex) | Derived; computed from `model_name + JSON(messages) + str(temperature)` |

**Key invariants**:
- Any change to `model_name`, any element of `messages`, or `temperature` produces a different `hash`.
- Two requests with identical field values always produce the same `hash`.
- The `hash` is the Redis key prefix after the namespace: `llm_cache:<hash>`.

---

### Message

A single conversation turn within the messages array.

| Field | Type | Constraints |
|---|---|---|
| `role` | enum | `system`, `user`, `assistant`, `tool` |
| `content` | string | Non-null; PII-redacted by Guardrails before reaching LiteLLM |

---

### CacheEntry

The value stored in Redis for a given CacheKey. Written on cache miss (after successful provider response); read on cache hit.

| Field | Type | Constraints |
|---|---|---|
| `response_body` | JSON object | Complete OpenAI-format response as returned to the caller |
| `model_name` | string | Provider model name as returned in the response (may differ from alias) |
| `created_at` | unix timestamp | Set at write time |
| `ttl_seconds` | integer | Set from `LITELLM_CACHE_TTL` environment variable; default 3600 |
| `expires_at` | unix timestamp | `created_at + ttl_seconds`; managed by Redis TTL command |

**Constraints**:
- `response_body` must be a complete, valid OpenAI chat completion JSON object (not partial or streaming chunks).
- Entries with `expires_at` in the past are automatically evicted by Redis; LiteLLM treats a missing key as a cache miss.
- Error responses (HTTP 4xx/5xx from provider) are never written as CacheEntry values.

---

### CacheStatusIndicator

Conveyed in the HTTP response header, not in the JSON body. The OpenAI response schema is unchanged.

| Header | Value on Cache Hit | Value on Cache Miss |
|---|---|---|
| `x-litellm-cache-hit` | `True` | `False` or absent |

---

### CacheMetrics

Prometheus counters emitted by LiteLLM's `prometheus` callback. Observable via `/metrics` on the LiteLLM internal port.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `litellm_cache_hit_count` | Counter | `model` | Increments once per request served from cache |
| `litellm_request_total` | Counter | `model`, `status` | Total requests (hit + miss); miss count derivable as `litellm_request_total - litellm_cache_hit_count` |

---

## Cache Entry Lifecycle

```
Incoming request
       │
       ▼
  Compute CacheKey.hash
       │
       ▼
  Redis GET llm_cache:<hash>
       │
   ┌───┴───────────────────────┐
   │ HIT                       │ MISS (key absent or expired)
   ▼                           ▼
Return CacheEntry.response_body  Call LiteLLM router → Provider
Set x-litellm-cache-hit: True    Receive provider response
Increment litellm_cache_hit_count
No Phoenix span                   Write CacheEntry to Redis
No Langfuse trace                 SET llm_cache:<hash> EX ttl_seconds
                                  Set x-litellm-cache-hit: False
                                  Create Phoenix span
                                  Create Langfuse trace
                                  Increment litellm_request_total
```

---

## TTL Configuration

```
Environment variable: LITELLM_CACHE_TTL
  │
  └── Resolved by LiteLLM at startup via os.environ/LITELLM_CACHE_TTL
         │
         └── Stored in cache_params.ttl
                │
                └── Applied as Redis TTL (EXPIRE command) on each cache write
```

**Default**: 3600 seconds (1 hour) if `LITELLM_CACHE_TTL` is not set.
**Override**: Set `LITELLM_CACHE_TTL=<seconds>` in `.env`; restart `litellm` container.
**Effect scope**: New writes only; existing Redis keys retain their original TTL.

---

## Redis Key Structure

```
Key format:   llm_cache:<sha256_hex>
Value format: JSON string (serialised CacheEntry.response_body)
TTL:          LITELLM_CACHE_TTL seconds (set at write time)
Eviction:     allkeys-lru (Redis evicts LRU key when maxmemory 256mb is reached)
```

**Example**:
```
llm_cache:a3f8d2c1... → {"id":"chatcmpl-...","object":"chat.completion",...}
```
