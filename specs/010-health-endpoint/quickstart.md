# Quickstart: Platform Health Endpoint (010)

## What changes

| File | Change |
|---|---|
| `services/kong/kong.yml` | Add `litellm-health` route under the `litellm` service — no `key-auth` plugin |
| `docker-compose.yml` | Update LiteLLM `healthcheck` block: `start_period: 20s`, `interval: 30s`, `retries: 5` |

No application code changes. LiteLLM's built-in `GET /health` is used as-is.

## Steps

### 1. Apply the Kong route change

In `services/kong/kong.yml`, add the following route inside the `litellm` service routes list (before the existing `models-catalogue` route):

```yaml
      - name: litellm-health
        paths:
          - /health
        methods:
          - GET
        strip_path: false
        # No key-auth plugin — unauthenticated access required
```

### 2. Apply the Docker healthcheck change

In `docker-compose.yml`, update the LiteLLM `healthcheck` block:

```yaml
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:4000/health || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 20s
```

### 3. Restart the core stack

```bash
make down
make up-core
make seed-kong
```

### 4. Verify

```bash
# Health via Kong (no auth header)
curl -s http://localhost:8080/health | jq .

# Confirm HTTP 200
curl -o /dev/null -w "%{http_code}\n" http://localhost:8080/health

# Confirm auth header is ignored (must not get 401)
curl -s -H "Authorization: Bearer anything" http://localhost:8080/health | jq .status

# Confirm Docker healthcheck is passing
docker ps --format "table {{.Names}}\t{{.Status}}" | grep litellm
# → should show "(healthy)"

# Memory check (must stay within core profile budget ~620 MB)
make stats
```

## Expected response

```json
{
  "status": "healthy",
  "healthy_endpoints": [...],
  "unhealthy_endpoints": [],
  "response_time_seconds": "0.85"
}
```

`status` is `"unhealthy"` when one or more model providers are unreachable — HTTP is still 200.
