# Quickstart: Streaming Chat Completions

**Feature**: `006-streaming-sse` | **Date**: 2026-05-28

## Prerequisites

- `make up-core` running (core profile)
- `.env` populated with `SMOKE_API_KEY` and at least one LLM provider key
- `curl` with `--no-buffer` support

---

## Scenario 1 — Basic Streaming Response (SC-001, SC-005)

Verify tokens arrive progressively with `Content-Type: text/event-stream`.

```bash
export SMOKE_API_KEY=<your-key>

curl -s --no-buffer \
  http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Count from 1 to 5 slowly."}],"stream":true}'
```

**Expected output** (one line per chunk):
```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","model":"gpt-4o-mini","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","model":"gpt-4o-mini","choices":[{"index":0,"delta":{"content":"1"},"finish_reason":null}]}

...

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","model":"gpt-4o-mini","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

---

## Scenario 2 — Measure Time to First Token / TTFT (SC-001)

Verify the first chunk arrives within 2 seconds.

```bash
curl -s --no-buffer \
  -w '\nTTFT: %{time_starttransfer}s\nTotal: %{time_total}s\n' \
  -o /dev/null \
  http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say hi."}],"stream":true}'
```

**Expected**: `TTFT:` value < 2.0 s.

Also query Prometheus for the histogram:
```bash
curl -s 'http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,litellm_request_latency_seconds_bucket{stream="true"})' | jq '.data.result[0].value[1]'
```

**Expected**: value < `2.0`.

---

## Scenario 3 — Verify [DONE] Sentinel and Content-Type (SC-005)

```bash
response=$(curl -s --no-buffer \
  http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say yes."}],"stream":true}')

# Check last data line is [DONE]
echo "$response" | grep "^data:" | tail -1
# Expected: data: [DONE]

# Verify Content-Type header
curl -s -I --no-buffer \
  http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say yes."}],"stream":true}' | \
  grep -i content-type
# Expected: content-type: text/event-stream
```

---

## Scenario 4 — Non-Streaming Still Works (SC-003)

Confirm existing non-streaming callers are unaffected.

```bash
curl -s \
  http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"What is 2+2?"}]}' | \
  jq '{content: .choices[0].message.content, finish_reason: .choices[0].finish_reason}'
```

**Expected**: complete JSON response, not SSE chunks.

---

## Scenario 5 — Cache Bypass for Streaming (FR-013)

Send the same prompt twice with `stream: true` — verify the second request is NOT served from cache (tokens still arrive progressively, not instantly).

```bash
for i in 1 2; do
  curl -s --no-buffer \
    -w "Request $i TTFT: %{time_starttransfer}s\n" \
    -o /dev/null \
    http://localhost:8080/v1/chat/completions \
    -H "Authorization: Bearer ${SMOKE_API_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Tell me about caching."}],"stream":true}'
done
```

**Expected**: Both TTFT values are > 0.1 s (not near-instant cache hits). A cached response would return in < 10 ms.

---

## Scenario 6 — Invalid Model Returns 400 Before Stream Opens (SC-004)

```bash
time curl -s -o /dev/null -w '%{http_code}' \
  http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"nonexistent-model-xyz","messages":[{"role":"user","content":"Hi"}],"stream":true}'
```

**Expected**: `400` returned in < 50 ms. No `text/event-stream` response body.

---

## Scenario 7 — Unauthenticated Streaming Returns 401

```bash
curl -s -o /dev/null -w '%{http_code}' \
  http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Hi"}],"stream":true}'
```

**Expected**: `401`. No SSE body sent.

---

## Scenario 8 — Phoenix Span Closed at Stream End (SC-002, obs profile)

```bash
# Start obs profile first: make up-obs

curl -s --no-buffer \
  http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Hi"}],"stream":true,"metadata":{"team":"ops","request_id":"req-stream-span-001"}}' \
  > /dev/null

sleep 2

# Query Phoenix for the most recent span
curl -s 'http://localhost:6006/v1/spans?limit=1' | \
  jq '.[0] | {model: .attributes["llm.model_name"], prompt_tokens: .attributes["llm.token_count.prompt"], completion_tokens: .attributes["llm.token_count.completion"], stream: .attributes["metadata.stream"]}'
```

**Expected**: All four fields populated, `stream: true`, both token counts > 0.

---

## Smoke Test (SC-005)

```bash
make smoke
```

Expected output includes:
```
[PASS]    POST /v1/chat/completions streaming — Content-Type: text/event-stream
[PASS]    POST /v1/chat/completions streaming — data: [DONE] received
[PASS]    POST /v1/chat/completions streaming — TTFT under 2s
```
