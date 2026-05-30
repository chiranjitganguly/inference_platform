# Quickstart: API Key Budget Enforcement & Spend Tracking

**Feature**: 009-key-budget-spend
**Date**: 2026-05-28
**Prerequisites**: `make up-core` (portal-backend is part of the `core` profile after this feature)

---

## 1. Verify environment

Ensure `.env` contains real values for:

```bash
LITELLM_MASTER_KEY=<your-master-key>
LITELLM_SALT_KEY=<random-32-char-string>   # New in this feature
DATABASE_URL=postgresql://user:pass@postgres:5432/litellm
```

Restart LiteLLM if `LITELLM_SALT_KEY` was not previously set:

```bash
make restart svc=litellm
```

---

## 2. Create a virtual key with a monthly budget

```bash
curl -s -X POST http://localhost:8080/v1/key/generate \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "key_alias": "team-alpha",
    "max_budget": 5.00,
    "budget_duration": "monthly",
    "models": ["gpt-4o-mini", "claude-haiku"]
  }' | python3 -m json.tool
```

**Expected response (HTTP 200)**:
```json
{
  "key": "sk-xxxxxxxxxxxxxxxxxxxxxxxx",
  "key_name": "team-alpha",
  "max_budget": 5.0,
  "budget_duration": "monthly",
  "budget_reset_at": "2026-06-01T00:00:00Z",
  "models": ["gpt-4o-mini", "claude-haiku"]
}
```

Save the `key` value — it is shown only once.

---

## 3. Make an inference request with the new key

```bash
TEAM_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxx"

curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${TEAM_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello!"}]
  }' | python3 -m json.tool
```

**Expected**: HTTP 200 with a normal chat completion response.

---

## 4. Link spend to a Langfuse prompt version

Pass prompt metadata in the request. LiteLLM's langfuse callback forwards it automatically:

```bash
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${TEAM_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Summarise this document."}],
    "metadata": {
      "langfuse_prompt_name": "document-summariser",
      "langfuse_prompt_version": "3"
    }
  }' | python3 -m json.tool
```

After the request, open the Langfuse dashboard (http://localhost:3002). The trace will carry:
- `cost` — USD cost of the request
- `usage.prompt_tokens` / `usage.completion_tokens`
- `input.metadata.langfuse_prompt_name: "document-summariser"`
- `input.metadata.langfuse_prompt_version: "3"`

Filter by prompt name in Langfuse to see aggregated cost per prompt version.

---

## 5. Check current spend

```bash
curl -s http://localhost:8080/v1/spend \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" | python3 -m json.tool
```

**Expected response**:
```json
{
  "period_start": "2026-05-01T00:00:00Z",
  "period_end": "2026-05-31T23:59:59Z",
  "total_spend_usd": 0.000123,
  "by_model": [
    {
      "model": "gpt-4o-mini",
      "spend_usd": 0.000123,
      "prompt_tokens": 12,
      "completion_tokens": 8
    }
  ],
  "by_key": [
    {
      "key_alias": "team-alpha",
      "key_hash": "sk-...xxxxx",
      "spend_usd": 0.000123,
      "max_budget_usd": 5.0,
      "budget_remaining_usd": 4.999877,
      "budget_reset_at": "2026-06-01T00:00:00Z"
    }
  ]
}
```

---

## 6. Verify budget enforcement (optional test)

Create a key with a very low budget, exhaust it, and confirm rejection:

```bash
# Create a key with $0.000001 budget (exhausted after ~1 token)
TINY_KEY=$(curl -s -X POST http://localhost:8080/v1/key/generate \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"key_alias":"tiny-budget-test","max_budget":0.000001,"budget_duration":"monthly"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['key'])")

# First request — may succeed or fail depending on cost
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${TINY_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'

# Second request — must return HTTP 429
HTTP_STATUS=$(curl -s -o /dev/null -w '%{http_code}' \
  -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${TINY_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi again"}]}')
echo "Expected 429, got: ${HTTP_STATUS}"
```

---

## 7. Run smoke tests

```bash
make smoke
```

Expect all spend-related probes to pass:
- `[PASS] GET /v1/spend — unauthenticated returns 401`
- `[PASS] GET /v1/spend — master key returns 200 with spend fields`
- `[PASS] POST /v1/key/generate — master key returns 200 with key`

---

## Acceptance Criteria (falsifiable)

| Criterion | Test |
|---|---|
| SC-001: Cost recorded per request | `GET /v1/spend` shows `by_key[].spend_usd > 0` after one inference request |
| SC-002: Budget exhaustion blocks requests | HTTP 429 returned on next request after budget exceeded |
| SC-003: Spend report under 500ms | `curl -o /dev/null -w '%{time_total}' http://localhost:8080/v1/spend -H "Authorization: Bearer ${LITELLM_MASTER_KEY}"` < 0.5 |
| SC-004: Monthly reset by 00:05 UTC | `by_key[].spend_usd == 0` on the 1st of the next month |
| SC-005: Langfuse traces carry cost | Langfuse trace for any completed request shows `cost` field > 0 |
| SC-006: Prompt version cost queryable | Filter Langfuse traces by `langfuse_prompt_name` and sum `cost` field |
