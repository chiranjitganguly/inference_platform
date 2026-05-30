# Feature Specification: Platform Health Endpoint

**Feature Branch**: `010-health-endpoint`

**Created**: 2026-05-30

**Status**: Draft

**Input**: User description: "Build a health endpoint that monitoring tools and load balancers can poll to determine whether the platform is operational. The endpoint must not require authentication. It must return a JSON body with a status field."

## Clarifications

### Session 2026-05-30

- Q: Does individual provider (OpenAI, Anthropic, etc.) unavailability cause the endpoint to return a non-2xx status? → A: No. The endpoint always returns HTTP 200 regardless of individual provider availability. Operational state is conveyed through the `status` field in the JSON body.
- Q: Is an Authorization header or any credential required to call the endpoint? → A: No. The endpoint must never require authentication of any kind.
- Q: When during the platform startup sequence must the endpoint become available? → A: The endpoint must be reachable before any other service (including the gateway and model router) is available.
- Q: Which service or process hosts the health endpoint? → A: The guardrails service (`services/guardrails/main.py`) — `/health` is added as a route to the existing FastAPI application.
- Q: What values can the `status` field hold? → A: Two values only — `"ok"` (fully operational) and `"degraded"` (issues present but service still responding).
- Q: Should the response body include a `dependencies` object enumerating subsystem statuses? → A: Yes — include a `dependencies` object with per-subsystem statuses (model router, cache) for operator visibility during incidents. Informational only; does not affect the HTTP status code or load balancer behaviour.
- Q: What tool uses this endpoint for container health verification? → A: Docker's HEALTHCHECK instruction polls this endpoint directly on localhost within the container.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Load Balancer Liveness Check (Priority: P1)

A load balancer periodically polls the platform to decide whether to route traffic to it. If the platform is unhealthy the load balancer removes it from the active pool until it recovers.

**Why this priority**: Without a health endpoint, load balancers have no machine-readable signal and must treat the platform as always available, masking outages.

**Independent Test**: Can be fully tested by sending an unauthenticated HTTP GET to `/health` and verifying a 200 response with a JSON body containing `"status": "ok"`.

**Acceptance Scenarios**:

1. **Given** the platform is fully operational, **When** a load balancer polls `/health` without credentials, **Then** it receives HTTP 200 with `{"status": "ok"}` within 1 second.
2. **Given** one or more individual providers are unavailable, **When** a load balancer polls `/health`, **Then** it still receives HTTP 200; the `status` field in the body indicates the degraded state.
3. **Given** any client sends a request with an `Authorization` header, **When** it polls `/health`, **Then** the endpoint behaves identically — no credential is required or inspected.

---

### User Story 2 - Monitoring Tool Alerting (Priority: P2)

An on-call engineer configures a monitoring tool (e.g., Prometheus blackbox exporter, Uptime Robot, Datadog synthetic) to alert when the platform stops responding healthily. The tool polls `/health` on a fixed interval and pages the engineer on non-2xx responses.

**Why this priority**: Automated alerting reduces mean time to detect (MTTD) for platform outages without requiring human-driven checks.

**Independent Test**: Can be fully tested by configuring a monitoring probe at `/health` and verifying it transitions from passing to alerting when the platform is stopped.

**Acceptance Scenarios**:

1. **Given** the monitoring tool polls every 30 seconds, **When** the platform is healthy, **Then** every poll returns HTTP 200 and the tool stays in a passing state.
2. **Given** the platform stops responding, **When** the monitoring tool polls, **Then** it receives a non-2xx response (or a timeout) and fires an alert.
3. **Given** the platform recovers, **When** the monitoring tool polls again, **Then** it returns to HTTP 200 and the alert clears automatically.

---

### User Story 3 - Dependency Status Visibility (Priority: P3)

A platform operator opens the health endpoint in a browser or runs `curl /health` to get a quick snapshot of which subsystems (e.g., database, cache) are reachable, without needing to log in.

**Why this priority**: Faster triage during incidents — operators can distinguish a total outage from a partial dependency failure in one request.

**Independent Test**: Can be fully tested by calling `/health` and verifying the response body contains named dependency statuses alongside the top-level `status` field.

**Acceptance Scenarios**:

1. **Given** all dependencies are reachable, **When** an operator calls `/health`, **Then** the JSON body contains a `status` field set to `"ok"` and a `dependencies` object where every dependency shows `"ok"`.
2. **Given** one dependency (e.g., cache) is unreachable, **When** an operator calls `/health`, **Then** the JSON body shows that dependency as `"unhealthy"` and the top-level `status` is `"degraded"`.
3. **Given** a dependency check takes too long, **When** `/health` is called, **Then** the endpoint still responds within 2 seconds and reports that dependency as `"timeout"`.

---

### Edge Cases

- What happens when the endpoint itself is reachable but every dependency is down? The top-level `status` must be `"degraded"` even though the HTTP layer returns 200.
- How does the system handle a dependency check that hangs indefinitely? Each dependency probe must have a hard timeout so the endpoint always returns in bounded time.
- What if the endpoint is called via HTTPS and the TLS certificate is expired? The health check fails at the transport layer before the application responds — this is expected and the load balancer treats it as unhealthy.
- What if the platform is starting up and not yet ready? The endpoint must be reachable and return HTTP 200 as soon as its host process starts — the `status` field signals the initialization state.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The platform MUST expose a health endpoint at a fixed, well-known path accessible without any authentication or authorization.
- **FR-002**: The health endpoint MUST always return HTTP 200. Operational state (healthy, degraded, starting) is conveyed exclusively through the `status` field in the JSON response body, never through the HTTP status code.
- **FR-003**: ~~Removed~~ — HTTP 503 is not returned for degraded or provider-unavailable states. (See FR-002.)
- **FR-004**: Every response from the health endpoint MUST include a JSON body with at minimum a `status` field whose value is one of exactly two strings: `"ok"` (platform fully operational) or `"degraded"` (platform responding but issues present).
- **FR-005**: The health endpoint MUST respond within 2 seconds under normal operating conditions.
- **FR-006**: The health endpoint MUST be hosted by the guardrails service and be reachable as soon as that service's process starts — before the platform gateway or any other dependent service has started. It MUST also be accessible through the platform gateway once the gateway is running.
- **FR-007**: The health endpoint MUST NOT require an API key, bearer token, session cookie, or any other credential to access.
- **FR-008**: The health endpoint MUST include a `dependencies` object in the response body listing the per-subsystem status of the model router and cache. These statuses are informational — they do not affect the HTTP status code. Additional subsystems may be added without requiring a spec change.
- **FR-009**: Each dependency probe MUST time out independently so that a slow dependency cannot cause the endpoint to hang.
- **FR-010**: The health endpoint MUST return HTTP 200 as soon as its host process starts, even before other services are initialized. During startup, dependency probes will return `"unhealthy"` or `"timeout"`, causing the top-level `status` to be `"degraded"` until all dependencies are reachable.
- **FR-011**: The health endpoint MUST be accessible on localhost within the guardrails container so that Docker's HEALTHCHECK instruction can poll it without going through any external gateway.

### Key Entities

- **Health Status**: Top-level operational state of the platform — one of `"ok"` (fully operational) or `"degraded"` (issues present; service still responding).
- **Dependency Status**: Per-subsystem reachability result — one of `"ok"`, `"unhealthy"`, or `"timeout"`. Named subsystems include at minimum: model router and cache. If any dependency is `"unhealthy"` or `"timeout"`, the top-level `status` is `"degraded"`.
- **Health Response**: The JSON document returned by the endpoint, containing `status` (required), `dependencies` (required), and optionally a `checked_at` timestamp.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The health endpoint responds in under 1 second for 99% of polls when all dependencies are healthy.
- **SC-002**: The health endpoint responds in under 2 seconds for 100% of polls even when one or more dependencies are timing out.
- **SC-003**: A load balancer or monitoring tool can determine platform health without any manual configuration of authentication credentials.
- **SC-004**: The `status` field transitions to `"degraded"` within 5 seconds of a critical dependency becoming unavailable, so that monitoring tools and operators receive an accurate signal before widespread user impact.
- **SC-005**: The `status` field accurately reflects the platform's operational state at all times — operators and automated tools always receive an honest signal via the response body, regardless of the HTTP status code.

## Assumptions

- The health endpoint path is `/health` unless a project-wide routing convention dictates otherwise.
- The health endpoint lives in the guardrails service (`services/guardrails/main.py`). The guardrails container starts before Kong and LiteLLM in the Docker Compose dependency order.
- The endpoint is also exposed through Kong (port 8080) with the authentication plugin bypassed for this specific route, so external load balancers and monitoring tools can reach it via the standard gateway path.
- The guardrails service binding to its port is the only prerequisite for the endpoint to respond — Kong is not required.
- Dependency probes are lightweight connectivity checks (e.g., ping/ping-equivalent), not deep functional tests, to keep response time bounded.
- The model router (LiteLLM) and the cache (Redis) are the minimum set of dependencies to probe; additional dependencies may be added without requiring a spec change.
- HTTPS termination is handled upstream of the application — the health endpoint itself does not need to manage TLS.
- The endpoint does not expose sensitive configuration details (no API keys, no model provider credentials, no internal IP addresses) in its response body.
