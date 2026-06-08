# Quickstart: Cache Flush Management

**Feature**: 022-cache-flush-management
**Prerequisites**: `make up-core && make seed-kong` | `SMOKE_API_KEY=<master-key>`

---

## Test 1: Full cache flush (SC-001, SC-004)

Populate the cache, flush it, verify cache miss.

```bash
# Step 1 — populate cache with a repeatable request
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $SMOKE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Reply with the word CACHED"}]}' \
  | jq '.choices[0].message.content'
# Expected: "CACHED" (or similar short response)

# Step 2 — flush the entire cache
curl -s -X DELETE http://localhost:8080/cache/flush \
  -H "Authorization: Bearer $SMOKE_API_KEY"
# Expected:
# {"keys_deleted": <integer ≥ 0>}

# Step 3 — re-issue the same request; must be a fresh call (not cached)
# Verify by checking response headers for X-Cache: MISS (if LiteLLM exposes cache hit header)
# or verify latency is consistent with a live LLM call
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $SMOKE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Reply with the word CACHED"}]}'
# Expected: HTTP 200 (fresh response — cache miss verified by non-zero latency)
```

---

## Test 2: Model-scoped cache flush (SC-002)

Flush only gpt-4o entries; claude-haiku entries must survive.

```bash
# Step 1 — populate cache for two models
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $SMOKE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say ALPHA"}]}'

curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $SMOKE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku","messages":[{"role":"user","content":"Say BETA"}]}'

# Step 2 — flush only gpt-4o-mini
curl -s -X DELETE "http://localhost:8080/cache/flush?model=gpt-4o-mini" \
  -H "Authorization: Bearer $SMOKE_API_KEY"
# Expected:
# {"keys_deleted": <integer ≥ 0>, "model": "gpt-4o-mini"}

# Step 3 — verify: gpt-4o-mini is a cache miss; claude-haiku is still cached
# (Re-issue both requests and compare response times or use LiteLLM /cache/ping if available)
```

---

## Test 3: Non-master key rejected with 403 (SC-003)

```bash
# Use a non-master consumer key (e.g., the smoke test consumer key if it is not master)
curl -s -w "\nHTTP %{http_code}\n" -X DELETE http://localhost:8080/cache/flush \
  -H "Authorization: Bearer sk-non-master-key-here"
# Expected:
# {"error":"forbidden","message":"Only the platform master key may flush the cache.","detail":{}}
# HTTP 403
```

---

## Test 4: No key rejected with 401

```bash
curl -s -w "\nHTTP %{http_code}\n" -X DELETE http://localhost:8080/cache/flush
# Expected:
# HTTP 401
```

---

## Test 5: Invalid model name rejected with 422 and valid list (SC-005, SC-006)

```bash
curl -s -w "\nHTTP %{http_code}\n" -X DELETE \
  "http://localhost:8080/cache/flush?model=nonexistent-model-xyz" \
  -H "Authorization: Bearer $SMOKE_API_KEY"
# Expected:
# {
#   "error": "invalid_model",
#   "message": "Unknown model name.",
#   "detail": {
#     "valid_models": ["gpt-4o", "gpt-4o-mini", "claude-sonnet", ...]
#   }
# }
# HTTP 422
```

---

## Test 6: Idempotency — flush empty cache returns 0 (FR-006)

```bash
# Flush once to clear
curl -s -X DELETE http://localhost:8080/cache/flush \
  -H "Authorization: Bearer $SMOKE_API_KEY"

# Flush again immediately
curl -s -X DELETE http://localhost:8080/cache/flush \
  -H "Authorization: Bearer $SMOKE_API_KEY"
# Expected:
# {"keys_deleted": 0}
# HTTP 200  (not an error)
```

---

## Test 7: Audit log entry produced (SC-004)

After any flush, query Loki for the audit record:

```logql
{service="portal-backend"} |= "cache_flush"
```

Expected log entry fields:
```json
{
  "event_type": "cache_flush_all",
  "keys_deleted": <integer>,
  "status_code": 200
}
```

No raw API key or cache key names should appear in the log entry.

---

## Acceptance Criteria Quick Reference

| SC | Criterion | Test |
|---|---|---|
| SC-001 | Full flush completes in < 5 s | Test 1 — measure with `time curl ...` |
| SC-002 | Scoped flush removes only target model | Test 2 — re-issue both models after flush |
| SC-003 | Non-master key rejected in < 100 ms | Test 3 — `time curl ...` with non-master key |
| SC-004 | 100% of flushes produce an audit entry | Test 7 — Loki query |
| SC-005 | Invalid model → HTTP 422 + valid list | Test 5 |
| SC-006 | 0% of re-issued requests served from cleared cache | Tests 1, 2 — cache miss verification |
