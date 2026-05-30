# Feature Specification: API Gateway Authentication

**Feature Branch**: `012-kong-api-gateway-auth`

**Created**: 2026-05-30

**Status**: Draft

**Input**: User description: "Build an API gateway in front of the model proxy so all client traffic passes through a single entry point that enforces authentication before any request reaches internal services. Every request to any inference endpoint must carry a valid API key or be rejected at the gateway. The health check endpoint must remain accessible without authentication. Internal service ports must not be reachable from outside the Docker network after this change."

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Authenticated API Consumer Accesses Inference (Priority: P1)

A developer or service with a valid API key sends a request to any inference endpoint and receives a successful response. The API key is validated at the gateway before the request is forwarded to any internal service.

**Why this priority**: This is the core access path. Without authenticated request flow working end-to-end, no other capability is reachable.

**Independent Test**: Can be tested by provisioning a single API key, sending a chat completion request through the gateway, and verifying a 200 response with a valid model reply.

**Acceptance Scenarios**:

1. **Given** a provisioned API key, **When** a request to `/v1/chat/completions` includes that key in the `Authorization` header, **Then** the gateway forwards the request and returns the model response with HTTP 200.
2. **Given** a provisioned API key, **When** a request to `/v1/embeddings` includes that key, **Then** the gateway forwards the request and returns embedding vectors with HTTP 200.
3. **Given** a provisioned API key, **When** a request to `/v1/models` includes that key, **Then** the gateway returns the model catalogue with HTTP 200.

---

### User Story 2 — Unauthenticated Request Is Rejected at the Gateway (Priority: P1)

Any client that sends a request to an inference endpoint without a valid API key receives a rejection response immediately at the gateway, before the request touches any internal service.

**Why this priority**: Equal priority to authenticated access — security enforcement must work before anything else reaches downstream services.

**Independent Test**: Can be fully tested by sending requests without credentials to each inference endpoint and confirming HTTP 401 responses.

**Acceptance Scenarios**:

1. **Given** no API key, **When** a request is sent to `/v1/chat/completions`, **Then** the gateway rejects it with HTTP 401 before forwarding.
2. **Given** an invalid or expired API key, **When** a request is sent to `/v1/embeddings`, **Then** the gateway rejects it with HTTP 401.
3. **Given** no API key, **When** a request is sent to `/v1/models`, **Then** the gateway rejects it with HTTP 401.

---

### User Story 3 — Health Check Remains Accessible Without Credentials (Priority: P2)

An operator, load balancer, or CI pipeline can verify that the platform is alive by calling the health endpoint without providing any API key.

**Why this priority**: Health monitoring must never be blocked by auth; it is used by infrastructure tooling that has no API key context.

**Independent Test**: Can be fully tested by sending an unauthenticated GET to `/health` and confirming a 200 response with liveness status.

**Acceptance Scenarios**:

1. **Given** no API key, **When** GET `/health` is called, **Then** the gateway returns HTTP 200 with a liveness indicator.
2. **Given** the platform is healthy, **When** GET `/health` is called from any network-reachable client, **Then** response time is under 2 seconds.

---

### User Story 4 — Internal Services Are Unreachable from Outside the Network (Priority: P2)

An external client attempting to connect directly to any internal service port (model proxy, internal admin interfaces) receives a connection refused or timeout — no data is returned, no auth prompt is shown.

**Why this priority**: Network isolation is a defence-in-depth requirement. Even if a valid key is somehow obtained, direct internal access must be impossible.

**Independent Test**: Can be tested by attempting direct connections to internal service ports from outside the Docker network and confirming all connections fail.

**Acceptance Scenarios**:

1. **Given** the platform is running, **When** a client outside the Docker network attempts to connect to the model proxy's internal port, **Then** the connection is refused or times out.
2. **Given** the platform is running, **When** a client outside the Docker network attempts to connect to any other internal service port, **Then** no response is received.

---

### Edge Cases

- What happens when an API key is present but malformed (wrong format, extra whitespace)?
- What happens when the `Authorization` header is present but contains a Bearer token instead of a raw key?
- What happens when the gateway is up but the upstream model proxy is down — does the 401 rejection still work?
- What happens when two requests arrive simultaneously, one valid and one invalid — are they handled independently?
- What is the error response body format for a rejected request — is it consistent across all endpoints?

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The gateway MUST reject any request to an inference endpoint that does not include a recognised API key, returning HTTP 401.
- **FR-002**: The gateway MUST forward requests that include a valid API key to the appropriate upstream service without exposing the key to that service.
- **FR-003**: The `/health` endpoint MUST be reachable without any API key and MUST return HTTP 200 when the platform is live.
- **FR-004**: All inference endpoints (`/v1/chat/completions`, `/v1/embeddings`, `/v1/models`) MUST require a valid API key.
- **FR-005**: The spend reporting endpoint (`/v1/spend`) MUST require a valid API key at the gateway level.
- **FR-006**: The key management endpoint (`/v1/key`) MUST require a valid API key at the gateway level.
- **FR-007**: The internal model proxy port MUST NOT be bound to the host network interface — it MUST only be reachable within the Docker network.
- **FR-008**: All other internal service ports MUST NOT be bound to the host network interface.
- **FR-009**: A named smoke-test consumer with a configurable API key MUST exist to support automated validation in CI/CD pipelines.
- **FR-010**: The API key MUST be presented in the `Authorization` request header.
- **FR-011**: The gateway MUST strip the API key from the request before forwarding it to upstream services so the key is not logged by internal services.
- **FR-012**: Rejection responses MUST include a consistent error body indicating the request was unauthorised.

### Key Entities

- **Consumer**: A named client identity registered at the gateway. Has one or more API keys bound to it. Examples: `smoke-test-consumer`, `platform-service`.
- **API Key**: A secret credential bound to a Consumer. Presented in the `Authorization` header on every request. Validated exclusively at the gateway.
- **Route**: A URL path pattern registered at the gateway that maps to an upstream service. Auth enforcement is configured per route.
- **Upstream Service**: An internal service (model proxy, portal backend) that receives requests only after the gateway has validated the caller. Never directly reachable from outside the Docker network.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of requests to inference endpoints without a valid API key receive an HTTP 401 response — zero unauthorised requests reach any internal service.
- **SC-002**: Rejection responses are returned in under 200 milliseconds, measured at the client, so failed auth does not create latency for attackers.
- **SC-003**: The `/health` endpoint responds HTTP 200 without credentials in under 2 seconds under normal operating conditions.
- **SC-004**: Direct connection attempts to any internal service port from outside the Docker network result in connection refused or timeout — 0% success rate for external direct access.
- **SC-005**: A smoke test run using the provisioned smoke-test API key completes with all inference endpoints returning HTTP 200.
- **SC-006**: After the change, no regression in authenticated request success rate — existing valid-key requests continue to succeed at 100%.

---

## Assumptions

- The gateway (Kong) is already deployed and serving traffic on port 8080 as the sole externally exposed port.
- API keys are long-lived secrets provisioned at setup time; self-service key rotation is out of scope for this feature.
- The `Authorization` header is used for key delivery; Bearer token format (OAuth2 / JWT) is out of scope — raw key strings only.
- A single smoke-test consumer is sufficient for CI/CD validation; multi-tenant consumer management is a future concern.
- The model proxy (LiteLLM) port binding to the host may exist currently from earlier development phases; this feature requires removing that binding.
- All other internal service ports (Redis, Postgres, Prometheus, Grafana, etc.) are assumed to already be internal-only; this feature confirms and documents that state for the model proxy specifically.
- Mobile or browser-based clients are out of scope; all consumers are server-side services or CLI tools.
- Rate limiting and quota enforcement are separate features; this spec covers authentication only.
