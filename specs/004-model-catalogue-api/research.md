# Research: Model Catalogue API

**Branch**: `004-model-catalogue-api` | **Date**: 2026-05-28

---

## 1. LiteLLM /v1/models — Native Endpoint Behaviour

**Decision**: Use LiteLLM's built-in `GET /v1/models` endpoint as the data source.

**Rationale**: LiteLLM natively exposes `/v1/models` in the OpenAI-compatible format. Each model in `config.yaml` can carry a `model_info` block whose fields are passed through in the response. This avoids building a separate catalogue service and keeps the model list as a single source of truth in config.

**How model_info passthrough works**:
```yaml
model_list:
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      tier: premium
      type: chat
      status: available
      context_window: 128000
      capabilities:
        - chat
        - streaming
        - function-calling
        - vision
```

LiteLLM includes the `model_info` dict verbatim in each model object returned by `/v1/models`. The envelope follows OpenAI format:
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
        "tier": "premium",
        "type": "chat",
        "status": "available",
        "context_window": 128000,
        "capabilities": ["chat", "streaming", "function-calling", "vision"]
      }
    }
  ]
}
```

**Alternatives considered**:
- **Portal-backend proxy**: Build a FastAPI endpoint that calls LiteLLM and reshapes the response. Rejected — adds a hop and a second service boundary for no functional gain; single source of truth argument favours config.yaml.
- **Static JSON file**: Maintain a separate catalogue JSON in the repo. Rejected — creates drift risk between the catalogue and the actual LiteLLM model list.

---

## 2. Kong key-auth Plugin

**Decision**: Use Kong's built-in `key-auth` plugin on the `/v1/models` route.

**Rationale**: `key-auth` is already the platform's auth mechanism for inference endpoints (per constitution §2.2). Applying the same plugin to `/v1/models` is consistent and requires no new credential type.

**Kong declarative config pattern**:
```yaml
_format_version: "3.0"
services:
  - name: litellm
    url: http://litellm:4000
    routes:
      - name: models-catalogue
        paths:
          - /v1/models
        methods:
          - GET
        plugins:
          - name: key-auth
            config:
              key_names: [Authorization]
              key_in_header: true
              hide_credentials: true
```

**Key format**: Clients send `Authorization: Bearer <key>`. Kong strips the header before forwarding to LiteLLM (hide_credentials: true).

**Alternatives considered**:
- **JWT validation**: Overkill for a read-only catalogue. JWT requires Keycloak to be running (auth profile), which violates the "core profile only" development constraint.
- **No auth**: Rejected — spec FR-005 and constitution §5.2 require auth at the Kong edge.

---

## 3. OpenAI model_info Field Position

**Decision**: Platform fields live inside `model_info` nested object, not at the top level of each model entry.

**Rationale**: The OpenAI `/v1/models` schema defines the top-level fields (`id`, `object`, `created`, `owned_by`). Adding platform fields at the top level risks colliding with future OpenAI additions. Nesting under `model_info` is additive, clearly scoped, and matches LiteLLM's own convention.

**Response shape** (final):
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
    },
    {
      "id": "text-embedding-3-large",
      "object": "model",
      "created": 1677610602,
      "owned_by": "openai",
      "model_info": {
        "provider": "openai",
        "tier": "premium",
        "type": "embedding",
        "status": "available",
        "context_window": 8191,
        "capabilities": ["embeddings"],
        "dimensions": 3072
      }
    }
  ]
}
```

---

## 4. Model Metadata Reference Table

All 11 platform models with their confirmed metadata values:

| id | provider | tier | type | status | context_window | capabilities | dimensions |
|---|---|---|---|---|---|---|---|
| gpt-4o | openai | premium | chat | available | 128,000 | chat, streaming, function-calling, vision | — |
| gpt-4o-mini | openai | standard | chat | available | 128,000 | chat, streaming, function-calling | — |
| gpt-4.1 | openai | premium | chat | available | 1,047,576 | chat, streaming, function-calling, vision | — |
| o4-mini | openai | standard | chat | available | 128,000 | chat, streaming, function-calling | — |
| claude-sonnet | anthropic | premium | chat | available | 200,000 | chat, streaming, function-calling, vision | — |
| claude-haiku | anthropic | standard | chat | available | 200,000 | chat, streaming | — |
| gemini-pro | google | premium | chat | available | 1,048,576 | chat, streaming, function-calling, vision | — |
| gemini-flash | google | standard | chat | available | 1,048,576 | chat, streaming, function-calling | — |
| command-r-plus | cohere | premium | chat | available | 128,000 | chat, streaming, function-calling | — |
| text-embedding-3-small | openai | standard | embedding | available | 8,191 | embeddings | 1,536 |
| text-embedding-3-large | openai | premium | embedding | available | 8,191 | embeddings | 3,072 |

**Tier classification rationale**:
- `premium`: highest capability or largest context per provider family; intended for complex reasoning tasks
- `standard`: faster, cheaper variant optimised for high-throughput or cost-sensitive workloads

---

## 5. docker-compose.yml Extension Strategy

**Decision**: Add `litellm` and `kong` service blocks to the existing `docker-compose.yml` under the `core` profile.

**Rationale**: Both services are core to the platform — without them no inference is possible. The `core` profile is where `postgres` already lives. Constitution §7.4 requires the platform to work with the `core` profile alone.

**Memory impact**: LiteLLM ~250 MB + Kong ~150 MB. Total new core footprint moves from ~50 MB (postgres only) to ~450 MB — still under the ~620 MB core budget.

**LiteLLM host binding**: LiteLLM port 4000 must have no host binding (constitution §2.1). Kong is the only service with a host-bound port (8080).

---

## 6. Smoke Test — Auth Header

**Finding**: The existing smoke-test.sh probe hits `/v1/models` without an API key. Once key-auth is enforced, this probe will return 401.

**Decision**: Update `smoke-test.sh` to pass a test API key via `SMOKE_API_KEY` env var. The smoke probe should:
1. Assert HTTP 200 with a valid key
2. Assert HTTP 401 with no key (new negative probe)

This keeps SC-003 and SC-005 both falsifiable.
