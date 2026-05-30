# Contract: GET /health

## Endpoint

| Attribute | Value |
|---|---|
| Method | `GET` |
| Path | `/health` |
| Authentication | None required |
| Content-Type | `application/json` |

## Request

No request body. No query parameters. No headers required.

The endpoint accepts and ignores any headers (including `Authorization`).

## Response

### HTTP Status Codes

| Code | Condition |
|---|---|
| `200` | Always — platform process is running |
| `5xx` / no response | LiteLLM process is down (Docker healthcheck treats this as unhealthy) |

### Response Body (always HTTP 200)

```json
{
  "status": "healthy",
  "healthy_endpoints": [
    {
      "model": "gpt-4o",
      "api_base": "https://api.openai.com"
    }
  ],
  "unhealthy_endpoints": [
    {
      "model": "gemini-pro",
      "api_base": "https://generativelanguage.googleapis.com",
      "error": "Connection timeout"
    }
  ],
  "response_time_seconds": "1.23"
}
```

### Response Body (degraded — still HTTP 200)

```json
{
  "status": "unhealthy",
  "healthy_endpoints": [],
  "unhealthy_endpoints": [
    {
      "model": "gpt-4o",
      "api_base": "https://api.openai.com",
      "error": "APIConnectionError"
    }
  ],
  "response_time_seconds": "5.01"
}
```

## Kong Configuration

The `/health` route is configured in `services/kong/kong.yml` under the `litellm` service:

```yaml
- name: litellm-health
  paths:
    - /health
  methods:
    - GET
  strip_path: false
  # No key-auth plugin — unauthenticated access required for load balancers
```

## Docker HEALTHCHECK

The LiteLLM container's healthcheck in `docker-compose.yml`:

```yaml
healthcheck:
  test: ["CMD-SHELL", "curl -sf http://localhost:4000/health || exit 1"]
  start_period: 20s
  interval: 30s
  timeout: 10s
  retries: 5
```

- `localhost:4000` — internal to container; port 4000 has no host binding
- `-s` — silent (no progress output)
- `-f` — fail on HTTP error (non-2xx exits non-zero)

## Acceptance Tests

```bash
# Via Kong gateway (external path)
curl -s http://localhost:8080/health | jq .

# Expected: HTTP 200, status field present
curl -o /dev/null -w "%{http_code}" http://localhost:8080/health
# → 200

# Auth header must be accepted and ignored
curl -s -H "Authorization: Bearer invalid-key" http://localhost:8080/health | jq .status
# → "healthy" or "unhealthy" (no 401)

# Direct LiteLLM (Docker healthcheck simulation)
docker exec inference_platform-litellm-1 curl -sf http://localhost:4000/health
# → exits 0 when healthy
```
