# Quickstart: Automatic Model Fallback Routing

**Feature**: `008-model-fallback-routing` | **Date**: 2026-05-28

## Prerequisites

- `make up-core` running (core profile)
- `.env` populated with `SMOKE_API_KEY` and at least one working LLM provider key
- `curl` and `jq` installed

---

## Scenario 1 — Silent Fallback on Provider Failure (US1, SC-001, SC-003)

Simulate a primary model failure by temporarily using an invalid key. The fallback model must serve the request and the `model` field in the response must identify the fallback.

```bash
# Send a request — primary is available, serves normally
curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"Reply with one word: OK"}]}' \
  | jq '.model, .choices[0].message.content'
# Expected: "gpt-4o" and "OK"

# To test fallback: invalidate OPENAI_API_KEY in .env, restart litellm, re-run.
# Expected with invalid OpenAI key:
#   .model = "claude-sonnet"   (first fallback in chain)
#   HTTP 200 with valid completion
#   x-request-id header present
```

---

## Scenario 2 — Fallback Model Identified in Response (SC-003)

Verify the `model` field reflects the actual fulfilling model, not the requested model.

```bash
RESPONSE=$(curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"Hi"}]}')

echo "Requested: gpt-4o"
echo "Fulfilled by: $(echo $RESPONSE | jq -r '.model')"
# When fallback fires: should print "claude-sonnet" or "gemini-pro"
# When primary serves: should print "gpt-4o"
```

---

## Scenario 3 — All Fallbacks Exhausted → HTTP 503 (US2, SC-002)

Configure all models in a chain to fail (e.g., invalidate all provider API keys). Verify 503 with platform error schema.

```bash
# With all provider keys invalid:
curl -s -o /dev/null -w "%{http_code}" \
  http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"Hi"}]}'
# Expected: 503

# Full response body:
curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"Hi"}]}' \
  | jq '.error, .message, .detail'
# Expected:
#   "all_fallbacks_exhausted"
#   "All models in the fallback chain are unavailable."
#   { requested_model: "gpt-4o", models_attempted: [...], failure_reasons: {...} }
```

---

## Scenario 4 — Context Window Overflow (US3, SC-004)

Submit a request that exceeds the primary model's context window. The platform must route to the larger-context alternative.

```bash
# Generate ~130k tokens of content (exceeds gpt-4o's 128k limit)
LONG_CONTENT=$(python3 -c "print('word ' * 40000)")

curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"gpt-4o\",\"messages\":[{\"role\":\"user\",\"content\":\"${LONG_CONTENT}\"}]}" \
  | jq '.model'
# Expected: "gpt-4.1"  (context_window_fallback for gpt-4o)
# HTTP 200 with valid completion
```

---

## Scenario 5 — Cooldown After 3 Consecutive Failures

After 3 consecutive failures, the model enters 60-second cooldown. Verify it is skipped in the chain.

```bash
# Trigger 3 failures on primary (e.g., invalid key for that provider only).
# On the 4th request within 60 seconds, primary should be skipped immediately
# and the first fallback should serve the request.
# Check LiteLLM logs for: "Model X is in cooldown" or similar message.
make logs svc=litellm | grep -i "cooldown\|fallback"
```

---

## Scenario 6 — Model with No Fallback Chain → Immediate 503

Embedding models have no fallback chain. A failure returns 503 immediately without retrying.

```bash
# With OPENAI_API_KEY invalid:
curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"text-embedding-3-small","input":"test"}' \
  -o /dev/null -w "%{http_code}"
# Expected: 503 (no fallback attempted — no chain configured)
```

---

## Scenario 7 — Phoenix Span Attributes Visible in Trace View

After a fallback event, verify the span attributes appear in Phoenix.

```bash
# Access Phoenix UI
open http://localhost:6006

# 1. Find a request that triggered fallback (look for spans where model ≠ requested model)
# 2. Click on the LLM span
# 3. Verify these attributes are present:
#    llm.fallback.triggered = True
#    llm.fallback.reason    = "provider_error" | "timeout" | "context_overflow"
#    llm.model.requested    = "gpt-4o"   (or whichever was requested)
#    llm.fallback.attempt_count = 2      (or however many attempts were made)
```

---

## Scenario 8 — Prometheus Fallback Counters

LiteLLM emits native Prometheus counters for fallback events.

```bash
# Query fallback success counter
curl -s 'http://localhost:9090/api/v1/query?query=litellm_fallback_success_total' \
  | jq '.data.result'

# Query fallback failure counter
curl -s 'http://localhost:9090/api/v1/query?query=litellm_fallback_failure_total' \
  | jq '.data.result'
```

---

## Smoke Test

```bash
make smoke
```

Expected fallback-related output:
```
[PASS]    POST /v1/chat/completions — primary model responds HTTP 200
[PASS]    POST /v1/chat/completions — 503 returned with structured error body when all fail
[PASS]    POST /v1/chat/completions — model field present in all successful responses
```
