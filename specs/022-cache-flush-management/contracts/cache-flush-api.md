# API Contract: Cache Flush Management

**Feature**: 022-cache-flush-management
**Service**: portal-backend (:8092) — exposed via Kong :8080
**Date**: 2026-06-08

---

## Base URL

All endpoints are accessed through Kong:

```
http://localhost:8080
```

---

## Authentication

All endpoints require the platform master key in the `Authorization` header:

```
Authorization: Bearer <LITELLM_MASTER_KEY>
```

| Condition | HTTP Status | `error` |
|---|---|---|
| No header | 401 | `unauthorized` |
| Valid consumer key, not master | 403 | `forbidden` |
| Master key | 200 / 422 / 503 | (proceed) |

---

## Endpoint 1: Full Cache Flush

### `DELETE /cache/flush`

Removes all `llm_cache:*` entries from the response cache. Idempotent — flushing an already-empty cache returns 200 with `keys_deleted: 0`.

**Request**

```
DELETE /cache/flush HTTP/1.1
Host: localhost:8080
Authorization: Bearer sk-master-...
```

No request body.

**Success Response (HTTP 200)**

```json
{
  "keys_deleted": 142
}
```

| Field | Type | Notes |
|---|---|---|
| `keys_deleted` | integer ≥ 0 | Number of cache entries removed |

**Error Responses**

| Status | Body |
|---|---|
| 401 | `{"error": "unauthorized", "message": "Master key required.", "detail": {}}` |
| 403 | `{"error": "forbidden", "message": "Only the platform master key may flush the cache.", "detail": {}}` |
| 503 | `{"error": "cache_unavailable", "message": "Cache backend unreachable.", "detail": {}}` |

---

## Endpoint 2: Model-Scoped Cache Flush

### `DELETE /cache/flush?model={name}`

Removes all `llm_cache:<model>:*` entries from the response cache for the specified model. Idempotent.

**Request**

```
DELETE /cache/flush?model=gpt-4o HTTP/1.1
Host: localhost:8080
Authorization: Bearer sk-master-...
```

No request body.

**Query Parameters**

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `model` | string | Yes | Must match a name in the LiteLLM model catalogue |

**Success Response (HTTP 200)**

```json
{
  "keys_deleted": 37,
  "model": "gpt-4o"
}
```

| Field | Type | Notes |
|---|---|---|
| `keys_deleted` | integer ≥ 0 | Number of cache entries removed for this model |
| `model` | string | Echo of the requested model name |

**Error Responses**

| Status | Body |
|---|---|
| 401 | `{"error": "unauthorized", "message": "Master key required.", "detail": {}}` |
| 403 | `{"error": "forbidden", "message": "Only the platform master key may flush the cache.", "detail": {}}` |
| 422 | `{"error": "invalid_model", "message": "Unknown model name.", "detail": {"valid_models": ["gpt-4o", "gpt-4o-mini", ...]}}` |
| 503 | `{"error": "cache_unavailable", "message": "Cache backend unreachable.", "detail": {}}` |
| 503 | `{"error": "model_catalogue_unavailable", "message": "Cannot validate model name — LiteLLM unreachable.", "detail": {}}` |

---

## Shared Behaviour

### Idempotency

Both endpoints are fully idempotent:
- Calling flush when the cache (or model scope) is already empty → HTTP 200, `keys_deleted: 0`
- Calling flush twice rapidly → second call returns `keys_deleted: 0` (first already cleared scope)

### Post-Flush Cache Miss Guarantee

After a successful flush, the **next identical inference request** (same model, same messages) results in a cache miss and a fresh upstream call. This is verified by:

1. Issue a request to populate the cache.
2. Call `DELETE /cache/flush` (or scoped variant).
3. Re-issue the identical request and observe that the response includes a fresh LLM-generated token sequence (not a cached repeat).

### Audit Logging

Every flush attempt produces a Loki audit entry regardless of outcome:

```json
{
  "timestamp": "2026-06-08T10:00:00Z",
  "event_type": "cache_flush_all",
  "request_id": "3e4f5a6b-...",
  "key_hash": "e3b0c44298...",
  "model": "all",
  "keys_deleted": 142,
  "status_code": 200
}
```

`key_hash` is SHA-256 of the raw `Authorization` value. Raw key is never logged.

---

## Kong Route Configuration

```yaml
# Added to services/kong/kong.yml under the existing portal-backend service entry:
routes:
  - name: cache-flush
    paths:
      - /cache/flush
    methods:
      - DELETE
    strip_path: false
    plugins:
      - name: key-auth
        config:
          key_names:
            - Authorization
          key_in_header: true
          hide_credentials: false   # portal-backend validates master key identity
      - name: rate-limiting
        config:
          minute: 10
          policy: local
```

`hide_credentials: false` — portal-backend needs the raw `Authorization` header to compare against `LITELLM_MASTER_KEY`. Kong still validates presence of a known consumer key first.
