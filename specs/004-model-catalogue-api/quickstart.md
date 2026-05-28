# Quickstart: Model Catalogue API

## Prerequisites

- Docker Desktop / OrbStack running
- `.env` file populated (copy from `.env.example`)
- `make up-core` has completed (postgres + litellm + kong)

## 1. Start the core stack

```bash
make up-core
```

Wait until all three services are healthy:

```bash
make ps
# Expected: postgres, litellm, kong all show "(healthy)"
```

## 2. Seed Kong with a test consumer and API key

```bash
make seed-kong
```

This registers a `test-consumer` in Kong and prints the generated API key. Copy it:

```
API_KEY=<printed-key>
```

## 3. Call the model catalogue

```bash
curl -s http://localhost:8080/v1/models \
  -H "Authorization: Bearer $API_KEY" | jq .
```

**Expected response shape:**
```json
{
  "object": "list",
  "data": [
    {
      "id": "gpt-4o",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai",
      "model_info": {
        "provider": "openai",
        "tier": "premium",
        "type": "chat",
        "status": "available",
        "context_window": 128000,
        "capabilities": ["chat", "streaming", "function-calling", "vision"]
      }
    }
    // ... 10 more entries
  ]
}
```

## 4. Verify authentication is enforced

```bash
curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/v1/models
# Expected: 401
```

## 5. Run the smoke test

```bash
SMOKE_API_KEY=$API_KEY make smoke
```

Look for:
```
[PASS]    LiteLLM /v1/models via Kong — authenticated (HTTP 200)
[PASS]    LiteLLM /v1/models via Kong — unauthenticated (HTTP 401)
```

## 6. Run the contract test

```bash
KONG_BASE_URL=http://localhost:8080 SMOKE_API_KEY=$API_KEY \
  pytest tests/contract/test_models_catalogue.py -v
```

All assertions should pass:
- ✓ Response is HTTP 200
- ✓ Envelope has `object: list`
- ✓ Exactly 11 models returned
- ✓ All four providers present
- ✓ Every entry has tier in {standard, premium}
- ✓ Every entry has type in {chat, embedding}
- ✓ Embedding models have dimensions; chat models do not
- ✓ Unauthenticated request returns 401

## 7. Check memory budget

```bash
make stats
# Confirm total across postgres + litellm + kong stays under ~620 MB
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `curl: (7) Connection refused` | Kong not running | `make up-core` and wait for healthy |
| HTTP 401 with valid key | key-auth plugin not seeded | `make seed-kong` |
| HTTP 404 on /v1/models | Kong route not configured | Check `services/kong/kong.yml` and reload Kong |
| Empty `data` array | LiteLLM config.yaml not mounted | Check volume mount in docker-compose.yml |
| Model missing from list | Not in config.yaml | Add model_list entry and restart litellm |
