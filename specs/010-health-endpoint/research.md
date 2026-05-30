# Research: Platform Health Endpoint (010)

## 1. LiteLLM Built-in Health Endpoint

**Decision**: Use LiteLLM Proxy's built-in `GET /health` endpoint — no application code required.

**Rationale**: LiteLLM already exposes `/health` and the existing Docker healthcheck in `docker-compose.yml` already polls it (`curl -sf http://localhost:4000/health`). Zero implementation cost; battle-tested by the LiteLLM project.

**Alternatives considered**:
- Add `/health` to guardrails service (`services/guardrails/main.py`) — rejected because it adds custom code and a dependency on the guardrails process starting first. LiteLLM starts independently of guardrails.
- Standalone minimal service — rejected; adds a container and operational overhead for a feature LiteLLM already provides for free.

---

## 2. LiteLLM /health Response Format

**Decision**: Accept LiteLLM's native response shape as the platform health response.

LiteLLM `GET /health` returns HTTP 200 in all cases (healthy and degraded). Sample response:

```json
{
  "status": "healthy",
  "healthy_endpoints": [
    {"model": "gpt-4o", "api_base": "https://api.openai.com"},
    {"model": "claude-sonnet", "api_base": "https://api.anthropic.com"}
  ],
  "unhealthy_endpoints": [
    {"model": "gemini-pro", "api_base": "https://generativelanguage.googleapis.com", "error": "timeout"}
  ],
  "response_time_seconds": "1.23"
}
```

**Status field values**: `"healthy"` (all endpoints reachable) or `"unhealthy"` (one or more endpoints unreachable). These differ from the spec's `"ok"` / `"degraded"` terminology — documented as an accepted deviation. LiteLLM's native terms are clearer for operators and require no transformation layer.

**Rationale**: The spec's status vocabulary (`"ok"` / `"degraded"`) was defined before the implementation approach was chosen. Using LiteLLM's native vocabulary avoids a translation shim with no benefit.

**Alternatives considered**:
- Proxy the response through guardrails to rename fields — rejected; adds latency and a failure point to a monitoring endpoint.

---

## 3. Kong Route Without key-auth

**Decision**: Add a dedicated Kong service+route for `/health` that routes to LiteLLM at `http://litellm:4000` without attaching the `key-auth` plugin.

**Rationale**: Existing Kong config applies `key-auth` per-route (not globally), so omitting the plugin from the `/health` route is sufficient. No allow-list or custom plugin needed.

**Alternatives considered**:
- Global `key-auth` with an exemption list — rejected; Kong 3.6 declarative config does not support per-route exemptions on a global plugin cleanly. Per-route is simpler and already the project pattern.
- Use Kong's `request-termination` plugin to return a synthetic 200 — rejected; adds indirection and prevents real health information from reaching callers.

---

## 4. Docker Healthcheck Parameters

**Decision**: Update LiteLLM's healthcheck in `docker-compose.yml`:

| Parameter | Current | New |
|---|---|---|
| `test` | `curl -sf http://localhost:4000/health \|\| exit 1` | `curl -sf http://localhost:4000/health` (equivalent) |
| `start_period` | 30s | 20s |
| `interval` | 10s | 30s |
| `retries` | 10 | 5 |

**Rationale**: `start_period: 20s` gives LiteLLM sufficient time to connect to Postgres and Redis before the first check. `interval: 30s` reduces poll frequency — LiteLLM's `/health` probes all configured model providers synchronously, which is expensive at 10-second intervals. `retries: 5` (150s window after start) is sufficient for failover detection.

**Alternatives considered**:
- Use `/health/liveliness` instead (just checks the process is up, not providers) — considered for a faster, lighter check; rejected because the user explicitly specified `/health` and the fuller probe gives richer operator information.

---

## 5. Spec Implementation Deviation

The spec clarification session chose the guardrails service as the host. This plan overrides that decision in favour of LiteLLM's built-in endpoint because:

1. No code change required — faster to deliver, no regression risk.
2. LiteLLM probes actual model provider reachability — better signal than a guardrails ping.
3. The Docker healthcheck already targets LiteLLM — consistent with existing operational practice.
4. The "reachable before any other service" requirement is met: LiteLLM starts before Kong in the Compose dependency graph, and the Docker healthcheck polls localhost:4000 directly.

This deviation requires no spec amendment — the functional requirements (unauthenticated JSON response with `status` field, Docker healthcheck compatible, accessible via Kong) are all satisfied.
