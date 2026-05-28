# Quickstart: Response Caching Layer

**Feature**: `007-response-caching` | **Date**: 2026-05-28

## Prerequisites

- `make up-core` running (core profile — includes redis-cache and redis-queue)
- `.env` populated with `SMOKE_API_KEY` and at least one LLM provider key
- `LITELLM_CACHE_TTL` set in `.env` (defaults to 3600 if unset)
- `curl` and `jq` installed

---

## Scenario 1 — Cache Miss Then Cache Hit (SC-001, SC-002)

Send the same request twice. The second response must arrive in under 10 ms and `x-litellm-cache-hit` must be `True`.

```bash
export SMOKE_API_KEY=<your-key>

# First request — cache miss (calls provider)
time curl -s \
  http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -D - \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"What is 2+2?"}],"temperature":0.0}' \
  | grep -E "x-litellm-cache-hit|\"content\""
```

**Expected** (first request):
```
x-litellm-cache-hit: False
"content": "2 + 2 equals 4."
```

```bash
# Second request — cache hit (no provider call)
time curl -s \
  http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -D - \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"What is 2+2?"}],"temperature":0.0}' \
  | grep -E "x-litellm-cache-hit|\"content\""
```

**Expected** (second request):
```
x-litellm-cache-hit: True
"content": "2 + 2 equals 4."
```
`time` output should show < 10 ms for the cache hit.

---

## Scenario 2 — Cache Miss on Different Model (SC-003)

A different model must not return the cached response.

```bash
# First request — cached under gpt-4o-mini key
curl -s \
  http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -D - \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"What is 3+3?"}],"temperature":0.0}' \
  | grep "x-litellm-cache-hit"
# Expected: x-litellm-cache-hit: False

# Second request — different model, must NOT hit cache
curl -s \
  http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -D - \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"What is 3+3?"}],"temperature":0.0}' \
  | grep "x-litellm-cache-hit"
# Expected: x-litellm-cache-hit: False  (different model = cache miss)
```

---

## Scenario 3 — Cache Miss on Different Temperature (SC-003)

```bash
# First request — temperature 0.0
curl -s \
  http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -D - \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say hello."}],"temperature":0.0}' \
  | grep "x-litellm-cache-hit"
# Expected: x-litellm-cache-hit: False

# Second request — temperature 0.7 (different key)
curl -s \
  http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -D - \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say hello."}],"temperature":0.7}' \
  | grep "x-litellm-cache-hit"
# Expected: x-litellm-cache-hit: False  (different temperature = cache miss)
```

---

## Scenario 4 — Streaming Requests Always Bypass Cache (SC-005)

```bash
# Send same streaming request twice — neither should be a cache hit
for i in 1 2; do
  curl -s --no-buffer \
    http://localhost:8080/v1/chat/completions \
    -H "Authorization: Bearer ${SMOKE_API_KEY}" \
    -H "Content-Type: application/json" \
    -D /tmp/headers_$i.txt \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Hi"}],"stream":true}' \
    > /dev/null
  echo "Request $i cache header: $(grep -i 'x-litellm-cache-hit' /tmp/headers_$i.txt || echo 'header absent')"
done
```

**Expected**: `x-litellm-cache-hit` header is absent on both streaming responses.

---

## Scenario 5 — Prometheus Cache Hit Counter (SC-007)

```bash
# Get current cache hit count before test
before=$(curl -s 'http://localhost:9090/api/v1/query?query=litellm_cache_hit_count' \
  | jq '.data.result[0].value[1] // "0"' -r)
echo "Cache hits before: $before"

# Send a cacheable request twice (second must be a hit)
curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Cache counter test."}],"temperature":0.0}' > /dev/null

curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Cache counter test."}],"temperature":0.0}' > /dev/null

# Get cache hit count after test
after=$(curl -s 'http://localhost:9090/api/v1/query?query=litellm_cache_hit_count' \
  | jq '.data.result[0].value[1] // "0"' -r)
echo "Cache hits after: $after"
echo "Delta: $(echo "$after - $before" | bc)"
```

**Expected**: Delta ≥ 1 (at least one cache hit registered).

---

## Scenario 6 — TTL Expiry (SC-004)

Configure a short TTL for testing, then wait for expiry.

```bash
# In .env, set: LITELLM_CACHE_TTL=30
# Then: make restart svc=litellm

# Send first request (cache miss + write with 30s TTL)
curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -D - \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"TTL test prompt."}],"temperature":0.0}' \
  | grep "x-litellm-cache-hit"
# Expected: False

# Second request (within 30s) — should be a hit
curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -D - \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"TTL test prompt."}],"temperature":0.0}' \
  | grep "x-litellm-cache-hit"
# Expected: True

# Wait for TTL to elapse
sleep 35

# Third request (after 30s TTL) — should be a miss again
curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -D - \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"TTL test prompt."}],"temperature":0.0}' \
  | grep "x-litellm-cache-hit"
# Expected: False  (TTL expired — fresh provider call)
```

---

## Scenario 7 — Cache Unavailable Fallback (SC-006)

Stop the Redis cache container and verify requests continue to succeed.

```bash
# Stop redis-cache container
docker stop inference_platform-redis-cache-1 2>/dev/null || docker stop $(docker ps -q --filter name=redis-cache)

# Request should still succeed (falls through to provider)
curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Fallback test."}]}' \
  | jq '.choices[0].message.content'
# Expected: non-empty string (provider response served normally)

# Restart redis-cache
make restart svc=redis-cache
```

---

## Smoke Test

```bash
make smoke
```

Expected cache-related output:
```
[PASS]    POST /v1/chat/completions cache — first request is cache miss
[PASS]    POST /v1/chat/completions cache — second request is cache hit (x-litellm-cache-hit: True)
[PASS]    POST /v1/chat/completions streaming — cache bypass confirmed (no cache-hit header)
```
