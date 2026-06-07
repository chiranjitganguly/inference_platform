# Feature Specification: WebSocket Streaming

**Feature Branch**: `021-websocket-streaming`

**Created**: 2026-06-07

**Status**: Draft

**Input**: User description: "Build WebSocket streaming so callers receive tokens over a persistent bidirectional connection. A client must connect, send a chat completion payload, and receive token deltas. The connection must remain open after a stream completes for subsequent requests."

## Clarifications

### Session 2026-06-07

- Q: What is the WebSocket endpoint path? → A: `/ws/v1/chat/completions`
- Q: How does authentication work at connection time? → A: Caller passes the existing platform API key in an HTTP header during the WebSocket upgrade handshake — no new credential type introduced.
- Q: What is the wire format for each message the caller receives? → A: Each message sent over the WebSocket is a discrete JSON object representing a token delta; control signals (stream complete, errors) are also JSON messages on the same connection.
- Q: Does the connection remain open after a stream completes? → A: Yes — the connection stays open and the caller can submit further requests without reconnecting.
- Q: What happens when a caller submits a second request while a stream is still in progress? → A: Option A — return a `stream_in_progress` error JSON message on the connection without closing it; the caller must wait for `stream_complete` before re-sending.
- Q: Maximum simultaneous WebSocket connections per API key? → A: 10 (configurable without a code change); excess connections are rejected at handshake time.
- Q: How are WebSocket streams traced in Phoenix? → A: Each stream request is traced as a single Phoenix span that closes when that stream ends; the connection itself is not a separate span.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Real-Time Token Streaming (Priority: P1)

A caller connects to the platform once and submits a chat completion request. As the model generates its response, each token delta is delivered to the caller incrementally over the open connection — so the caller sees words appearing in real time rather than waiting for the full response to finish.

**Why this priority**: This is the core value of the feature. Everything else (multi-request reuse, error recovery) depends on this foundational flow working correctly first. Without it the feature delivers no user value.

**Independent Test**: Can be fully tested by opening a single connection, sending one chat payload, and verifying that multiple token-delta messages arrive in sequence before the completion signal — delivers complete end-to-end value as an MVP.

**Acceptance Scenarios**:

1. **Given** a caller has established a connection to the streaming endpoint, **When** the caller sends a valid chat completion payload, **Then** the platform begins delivering individual token-delta messages within 500 ms and continues until a completion signal is sent.
2. **Given** a stream is in progress, **When** the model produces each successive token, **Then** the caller receives a discrete message containing only that token delta (not the full response so far).
3. **Given** the model has finished generating, **When** the final token is delivered, **Then** the platform sends a distinct "stream complete" signal so the caller knows the full response has been received.

---

### User Story 2 - Persistent Connection for Multiple Requests (Priority: P2)

After receiving a complete streamed response, the caller's connection remains open. The caller can immediately submit another chat completion payload without needing to reconnect — reusing the same session for many back-to-back interactions.

**Why this priority**: Connection reuse eliminates reconnection latency and is the key differentiator over server-sent events or plain HTTP streaming. It is high-value but the feature is still useful without it if story 1 is complete.

**Independent Test**: Can be fully tested by sending two sequential chat payloads over one connection and verifying that both generate independent, correctly sequenced token streams without any reconnect occurring between them.

**Acceptance Scenarios**:

1. **Given** a caller has just received the "stream complete" signal for a request, **When** the caller sends a second chat completion payload on the same connection, **Then** a new token stream begins without any reconnection step.
2. **Given** a connection has been idle for less than the platform's idle timeout, **When** the caller sends a new payload, **Then** the platform treats it as a fresh request and begins streaming immediately.
3. **Given** multiple sequential requests are issued over one connection, **Then** each request's token stream is isolated — tokens from request N+1 never interleave with tokens from request N.

---

### User Story 3 - Graceful Error and Disconnection Handling (Priority: P3)

When a streaming request fails (invalid payload, model error, rate limit, or upstream timeout), the caller receives a structured error message over the connection. The connection itself remains open so the caller can retry or send a different request without reconnecting.

**Why this priority**: Error transparency is important for production callers, but the platform still delivers significant value if stories 1 and 2 are complete. This story polishes reliability and caller experience.

**Independent Test**: Can be fully tested by sending an intentionally malformed payload and verifying that a structured error message is returned over the existing connection while the connection stays open for a subsequent valid request.

**Acceptance Scenarios**:

1. **Given** a caller sends a payload that fails validation, **When** the platform detects the error, **Then** a structured error message (with a machine-readable error code and human-readable description) is sent over the connection and no stream is started.
2. **Given** the upstream model API returns an error mid-stream, **When** the platform receives that error, **Then** it delivers a "stream error" signal to the caller with the reason, and the connection remains open.
3. **Given** the caller closes the connection unexpectedly mid-stream, **When** the platform detects the disconnection, **Then** it stops forwarding tokens and releases all resources associated with that stream cleanly.

---

### User Story 4 - Caller Authentication and Connection Identity (Priority: P2)

A caller must present valid credentials at connection time. The platform verifies identity before any streaming begins. Authenticated connection context (caller identity, authorised models, rate-limit quota) persists for the lifetime of the connection so it need not be re-validated per request.

**Why this priority**: Without authentication the endpoint is open to abuse; this must be in place before the streaming endpoint is exposed. It shares priority P2 with connection reuse because both are required before production deployment.

**Independent Test**: Can be fully tested by attempting to connect without credentials and verifying rejection, then connecting with valid credentials and verifying that a chat completion payload is accepted and streamed.

**Acceptance Scenarios**:

1. **Given** a client attempts to connect without valid credentials, **When** the handshake completes, **Then** the platform rejects the connection with an authentication-failure message and closes it.
2. **Given** a client connects with valid credentials, **When** the connection is established, **Then** the caller's identity and authorised model list are resolved and associated with the session.
3. **Given** a credential expires while a connection is live, **When** the caller submits the next request, **Then** the platform sends an "authentication expired" error and closes the connection gracefully.

---

### Edge Cases

- What happens when the caller sends a second payload while a stream from the first is still in progress? (Expected: the platform returns a `stream_in_progress` error JSON message on the connection without closing it; the new request is not started; the active stream continues unaffected.)
- How does the system handle a model that never produces a first token within the platform's response-start timeout? (Expected: a timeout error is sent and the connection stays open.)
- What happens when the connection is dropped by a network intermediary (proxy close, TCP reset) mid-stream? (Expected: the platform detects the loss and cleans up resources within a defined window.)
- How does the system behave when a caller opens many simultaneous connections from the same credential? (Expected: the platform enforces a default limit of 10 simultaneous open connections per API key, configurable without a code change; the 11th connection attempt is rejected at handshake time with a `connection_limit_exceeded` error.)
- What happens when the payload requests a model not authorised for the caller? (Expected: an authorisation error is returned immediately without starting a stream, connection stays open.)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The platform MUST expose a persistent bidirectional streaming endpoint at the path `/ws/v1/chat/completions` that callers connect to once and reuse for multiple sequential chat completion requests.
- **FR-002**: The platform MUST deliver each token delta as a discrete JSON message to the caller as soon as it is produced by the model — not buffered until the full response is ready. Every message on the connection (token delta, stream complete, error) MUST be a well-formed JSON object.
- **FR-003**: The platform MUST send a distinct "stream complete" signal when the model has finished generating a response, clearly demarcating the end of one stream.
- **FR-004**: The platform MUST keep the caller's connection open after a stream completes so that subsequent requests can be submitted without reconnecting.
- **FR-005**: The platform MUST accept the caller's existing platform API key as an HTTP header during the WebSocket upgrade handshake, and MUST reject connections that present no key or an invalid key before any request is processed.
- **FR-006**: The platform MUST propagate caller identity and authorised model scope to all downstream components for every request made over the connection, using the same authentication resolved at connect time.
- **FR-007**: The platform MUST return structured error messages (machine-readable code + human-readable description) over the existing connection when a request fails, without closing the connection.
- **FR-008**: The platform MUST enforce per-caller and per-connection rate limits consistent with the limits already applied to non-streaming requests through the gateway. The platform MUST enforce a default limit of 10 simultaneous open WebSocket connections per API key (configurable without a code change); connections beyond this limit MUST be rejected at handshake time with a `connection_limit_exceeded` error message.
- **FR-009**: The platform MUST route streaming requests through the full request pipeline (authentication, policy check, PII scanning, model routing) — the streaming path MUST NOT bypass any existing guardrail.
- **FR-010**: The platform MUST clean up all stream-related resources (memory, upstream connections, worker state) within a defined window when a caller connection closes, whether gracefully or abruptly.
- **FR-011**: The platform MUST emit structured telemetry for each streaming session: connection opened, request started, first token received, stream completed/errored, connection closed — with timestamps and caller identity (no prompt content). Each stream request MUST be traced in Phoenix Arize as a single span that opens when the request is accepted and closes when the stream ends (complete or error); the connection lifetime is not a separate span.
- **FR-012**: The platform MUST enforce a configurable idle timeout on connections that have sent no request for a defined period, closing them with an appropriate signal.
- **FR-013**: When a caller submits a new request while a stream is already in progress on the same connection, the platform MUST return a `stream_in_progress` error JSON message on the connection without interrupting the active stream and without closing the connection.

### Key Entities

- **Streaming Connection**: A long-lived bidirectional channel between a caller and the platform; carries multiple sequential requests over its lifetime. Attributes: connection ID, caller identity, authenticated-at timestamp, last-activity timestamp, state (idle / streaming / closing).
- **Stream Request**: A single chat completion request submitted over an open connection. Attributes: request ID, connection ID, model requested, timestamp submitted, state (pending / streaming / complete / error).
- **Token Delta**: An individual token or partial token produced by the model and forwarded to the caller as a JSON message. Attributes: request ID, sequence number, token content, timestamp.
- **Stream Event**: A control JSON message sent over the connection — types include: `stream_start`, `token_delta`, `stream_complete`, `stream_error`, `auth_error`, `rate_limit_error`, `validation_error`, `connection_closing`. All event types share the same JSON envelope so callers can handle them with a single message parser.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Callers receive the first token delta within 600 ms of the platform accepting a valid request (measured as 95th-percentile latency under normal load).
- **SC-002**: A single connection can serve at least 50 sequential chat completion requests before requiring reconnection under standard usage.
- **SC-003**: Token delivery throughput matches the model's native generation speed — no artificial buffering that increases perceived latency by more than 50 ms per token.
- **SC-004**: 100% of streaming requests are routed through the existing guardrail and policy pipeline — zero bypasses detectable in audit logs.
- **SC-005**: Unauthenticated connection attempts are rejected at handshake time in under 200 ms.
- **SC-006**: After a caller disconnects (clean or abrupt), all associated resources are released within 10 seconds with no memory or goroutine leaks under sustained load.
- **SC-007**: Streaming endpoint sustains at least 200 concurrent active streams on the platform's standard deployment footprint without exceeding the existing memory budget.
- **SC-008**: All stream lifecycle events (open, request, first-token, complete/error, close) appear in the platform's telemetry store with correct timestamps and caller ID, and zero prompt content. Phoenix Arize shows one span per stream request with accurate start and end times; no span is created for the connection itself.
- **SC-009**: An API key that already holds 10 open connections has any further connection attempt rejected at handshake time with a `connection_limit_exceeded` response; the 10 existing connections are unaffected.

## Assumptions

- Callers are authenticated applications or services, not end-users directly; human-facing clients connect via an intermediary that handles the WebSocket session.
- The existing Kong → Guardrails → LiteLLM request pipeline handles the actual model call; the streaming endpoint is an additional transport layer that integrates with this pipeline rather than bypassing it.
- Sequential requests within one connection are processed one at a time (no multiplexing of concurrent streams over a single connection in v1); a caller that needs concurrent streams opens multiple connections.
- Authentication uses the caller's existing platform API key, passed as an HTTP header during the WebSocket upgrade handshake; no new credential type is introduced and no separate auth step occurs per request.
- Mobile or browser clients accessing the streaming endpoint will do so via the Kong gateway at port 8080; the endpoint is not exposed directly.
- Prompt content is never written to any log, trace, or metric store — the same invariant that applies to all other platform paths applies here.
- The platform's existing rate-limit and spend-tracking infrastructure is reused; the streaming endpoint does not introduce a separate quota system.
- Connection-level idle timeout defaults to 5 minutes; this is configurable without a code change.
- Per-API-key simultaneous connection limit defaults to 10; this is configurable without a code change.
