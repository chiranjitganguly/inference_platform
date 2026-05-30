# Data Model: Platform Health Endpoint (010)

## Entities

### HealthResponse

The JSON document returned by `GET /health` (LiteLLM native format).

| Field | Type | Required | Description |
|---|---|---|---|
| `status` | `"healthy" \| "unhealthy"` | Yes | Top-level platform state |
| `healthy_endpoints` | `Endpoint[]` | Yes | Model endpoints that responded successfully |
| `unhealthy_endpoints` | `Endpoint[]` | Yes | Model endpoints that failed or timed out |
| `response_time_seconds` | `string` | Yes | Wall-clock time LiteLLM spent probing all endpoints |

### Endpoint

A single model provider endpoint entry within the health response.

| Field | Type | Required | Description |
|---|---|---|---|
| `model` | `string` | Yes | Model name as configured in LiteLLM catalogue |
| `api_base` | `string` | Yes | Provider API base URL |
| `error` | `string` | No | Present only on unhealthy endpoints; reason for failure |

## Status Semantics

| `status` value | Meaning | HTTP code |
|---|---|---|
| `"healthy"` | All configured model endpoints responded within timeout | 200 |
| `"unhealthy"` | One or more model endpoints failed or timed out | 200 |

**Note**: HTTP status is always 200. The `status` field is the sole machine-readable health signal.

## State Transitions

```
[process starts]
      │
      ▼
  status: "unhealthy"     ← dependencies not yet reachable during startup
      │
      │  all providers respond
      ▼
  status: "healthy"
      │
      │  one or more providers fail/timeout
      ▼
  status: "unhealthy"     ← informational; HTTP still 200
```

## No Persistent Storage

The health response is computed on demand. No entity is written to PostgreSQL, Redis, Loki, or any other store. Each poll produces a fresh response.
