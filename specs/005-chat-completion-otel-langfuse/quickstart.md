# Quickstart: Chat Completion with Observability

**Feature**: `005-chat-completion-otel-langfuse` | **Date**: 2026-05-28

## Prerequisites

- Docker Desktop running, `make` available
- `.env` populated (copy `.env.example`, fill in `SMOKE_API_KEY` and at least one LLM provider key)
- `obs` profile brings up Phoenix and Langfuse alongside the core stack

---

## Start the Platform

```bash
# Core inference path (Kong + LiteLLM + Postgres)
make up-core

# Add observability sinks (Phoenix Arize + Langfuse)
make up-obs
```

Seed Kong consumer (first time only):
```bash
make seed-kong
```

---

## Scenario 1 — Basic Chat Completion (SC-001, SC-006)

Verify the inference path returns a complete response with required fields.

```bash
export SMOKE_API_KEY=<your-key>

curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "What is 2+2?"}]
  }' | jq '{
    content: .choices[0].message.content,
    finish_reason: .choices[0].finish_reason,
    prompt_tokens: .usage.prompt_tokens,
    completion_tokens: .usage.completion_tokens
  }'
```

**Expected**: all four fields non-null; `finish_reason` is `stop`; `content` contains the answer.

---

## Scenario 2 — Phoenix Arize Span Verification (SC-002)

After Scenario 1, confirm the span landed in Phoenix.

```bash
# Query Phoenix GraphQL for the most recent span
curl -s http://localhost:6006/v1/spans \
  -H "Content-Type: application/json" \
  -d '{"limit": 1}' | jq '.[0] | {
    model: .attributes["llm.model_name"],
    prompt_tokens: .attributes["llm.token_count.prompt"],
    completion_tokens: .attributes["llm.token_count.completion"]
  }'
```

**Expected**: `llm.model_name` = `"gpt-4o-mini"`, both token counts positive integers matching the API response.

---

## Scenario 3 — Metadata Tagging in Phoenix (SC-005)

Send a request with team and request_id metadata, then verify both appear on the Phoenix span.

```bash
curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-haiku",
    "messages": [{"role": "user", "content": "Hello"}],
    "metadata": {
      "team": "platform-team",
      "request_id": "req-quickstart-001"
    }
  }' | jq '.choices[0].message.content'

# Then inspect Phoenix for the span with team tag
curl -s "http://localhost:6006/v1/spans?limit=1" | \
  jq '.[0].attributes | {"team": .["metadata.team"], "request_id": .["metadata.request_id"]}'
```

**Expected**: both tags non-null and matching the sent values.

---

## Scenario 4 — Langfuse Trace Verification (SC-003)

After any successful request, confirm a Langfuse trace was created.

```bash
# List recent traces via Langfuse API
curl -s http://localhost:3002/api/public/traces \
  -u "${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}" | \
  jq '.data[0] | {id: .id, model: .metadata.model, team: .metadata.team}'
```

**Expected**: a trace exists with `model` and `team` (if sent) populated.

---

## Scenario 5 — Prompt-Linked Langfuse Trace (SC-003)

First, register a production prompt in Langfuse:
```bash
python scripts/prompt-register.py  # creates "system-v1" at draft label
python scripts/prompt-promote.py   # promotes to production label
```

Then send a request with `prompt_name`:
```bash
curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Explain photosynthesis briefly."}],
    "metadata": {
      "team": "data-science",
      "request_id": "req-quickstart-002",
      "prompt_name": "system-v1"
    }
  }' | jq '.choices[0].message.content'
```

Query the resulting Langfuse trace:
```bash
curl -s http://localhost:3002/api/public/traces \
  -u "${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}" | \
  jq '.data[0] | {promptName: .metadata.promptName, promptVersion: .metadata.promptVersion}'
```

**Expected**: `promptName` = `"system-v1"` and `promptVersion` is a positive integer.

---

## Scenario 6 — Invalid Model Returns 400 (SC-004)

```bash
time curl -s -o /dev/null -w '%{http_code}' \
  http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model": "nonexistent-model-xyz", "messages": [{"role": "user", "content": "Hi"}]}'
```

**Expected**: HTTP 400, response time < 50 ms (no upstream call made).

---

## Scenario 7 — Unauthenticated Request Returns 401 (FR-009)

```bash
curl -s -o /dev/null -w '%{http_code}' \
  http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hi"}]}'
```

**Expected**: HTTP 401 — Kong rejects before reaching LiteLLM.

---

## Smoke Test (SC-006)

The standard smoke test script includes the chat completion probe:

```bash
make smoke
```

Expected output includes a `[PASS]` line for `POST /v1/chat/completions`.

---

## Observability UI Access

| Tool | URL | Purpose |
|---|---|---|
| Phoenix Arize | http://localhost:6006 | LLM traces, spans, token usage |
| Langfuse | http://localhost:3002 | Prompt-linked traces, evaluation scores |
| Grafana | http://localhost:3000 | Metrics dashboards (prometheus data source) |

> Phoenix and Langfuse UIs are for deep inspection only. Routine monitoring
> uses Grafana dashboards (constitution requirement).
