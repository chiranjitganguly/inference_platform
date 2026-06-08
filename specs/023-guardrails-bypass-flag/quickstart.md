# Quickstart: Guardrails Bypass Flag

**Feature**: 023-guardrails-bypass-flag
**Prerequisites**: `make up-core && make seed-kong` | Stack running with guardrails service healthy

---

## Test 1: True streaming bypass (SC-001)

Verify SSE chunks arrive at distinct timestamps — not all at once.

```bash
python -c "
import asyncio, httpx, json, os, time
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path('.env'))

async def debug():
    headers = {'Authorization': os.environ['PLATFORM_API_KEY'], 'Content-Type': 'application/json'}
    payload = {
        'model': 'claude-sonnet',
        'max_tokens': 300,
        'messages': [{'role': 'user', 'content': 'Write a short paragraph about Kolkata'}],
        'stream': True,
        'guardrails': False,
    }
    async with httpx.AsyncClient(timeout=60) as c:
        async with c.stream('POST', 'http://localhost:8080/v1/chat/completions',
                             headers=headers, json=payload) as r:
            async for line in r.aiter_lines():
                if line.startswith('data: ') and line != 'data: [DONE]':
                    chunk = json.loads(line[6:])
                    delta = chunk.get('choices', [{}])[0].get('delta', {}).get('content', '')
                    if delta:
                        print(f'[{time.time():.3f}] {repr(delta)}')

asyncio.run(debug())
"
# Expected: multiple lines with DIFFERENT timestamps, proving true streaming
# e.g.:
# [1780887559.998] '# Kolkata\n\nKolkata'
# [1780887559.999] ', formerly known as Calcutta,'
# [1780887560.000] ' was founded in 1690...'
```

---

## Test 2: Flag stripped before forwarding (SC-002)

Verify LiteLLM does not receive the `guardrails` key.

```bash
# Check LiteLLM debug logs for a bypass request — no "guardrails" field in logged body
docker logs inference_platform-litellm-1 2>&1 | grep -A5 "guardrails" | head -20
# Expected: no output (guardrails key absent from forwarded body)
```

---

## Test 3: Non-streaming bypass

```bash
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: smoke-test-key-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet",
    "max_tokens": 50,
    "messages": [{"role": "user", "content": "Say hello"}],
    "guardrails": false
  }' | python3 -m json.tool
# Expected: HTTP 200, well-formed completion response, no guardrails-related errors
```

---

## Test 4: Guardrails-on behaviour unchanged (SC-003)

```bash
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: smoke-test-key-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet",
    "max_tokens": 50,
    "messages": [{"role": "user", "content": "Say hello"}]
  }' | python3 -m json.tool
# Expected: HTTP 200 — identical behaviour to pre-feature (guardrails flag absent = guardrails on)
```

---

## Test 5: Guardrails-on with explicit true

```bash
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: smoke-test-key-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet",
    "max_tokens": 50,
    "messages": [{"role": "user", "content": "Say hello"}],
    "guardrails": true
  }' | python3 -m json.tool
# Expected: HTTP 200 — identical to Test 4; explicit true = default behaviour
```

---

## Test 6: Reference client

```bash
# Ensure PLATFORM_API_KEY is in .env
grep PLATFORM_API_KEY .env

# Run the reference streaming client
python app/test.py
# Expected: streaming prose response printed to terminal word-by-word
```

---

## Test 7: Audit log still fires on bypass (SC-004)

After running any bypass request, query Loki:

```logql
{service="guardrails"} |= "inference_request" | json | line_format "{{.key_hash}} {{.model_name}}"
```

Expected: an entry appears for each bypass request. The `pii_entity_count` and `scanner_blocked` fields are always 0 on the bypass path.

---

## Acceptance Criteria Quick Reference

| SC | Criterion | Test |
|---|---|---|
| SC-001 | Multiple SSE chunks at distinct timestamps | Test 1 — timestamp each chunk |
| SC-002 | `guardrails` key absent from forwarded body | Test 2 — LiteLLM logs |
| SC-003 | No regression in guardrails-on behaviour | Tests 4, 5 |
| SC-004 | Audit log entry per bypass request | Test 7 — Loki query |
| SC-005 | httpx connections cleaned up after drain | Inspect with `make stats` — no connection leak |
