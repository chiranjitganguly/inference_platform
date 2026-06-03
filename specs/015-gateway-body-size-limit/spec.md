# Feature Specification: Gateway Request Body Size Limit

**Feature Branch**: `015-gateway-body-size-limit`

**Created**: 2026-06-02

**Status**: Draft

**Input**: User description: "Build a request body size limit at the gateway to prevent oversized payloads from reaching the model proxy. Requests exceeding the configured limit must be rejected immediately at the gateway with an appropriate HTTP error. This protects against context-stuffing attacks."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Oversized Payload Rejected at Gateway (Priority: P1)

An API consumer sends a request to an inference route with a payload larger than 10 MB. The gateway immediately rejects the request without forwarding any part of it to the model proxy or any internal service, returning a 413 status code with a clear error message.

**Why this priority**: This is the core protection goal. Without this, context-stuffing attacks can reach the model proxy and exhaust compute resources or manipulate model context.

**Independent Test**: Can be fully tested by sending a >10 MB POST body to any inference route and confirming a 413 response with no upstream traffic observed.

**Acceptance Scenarios**:

1. **Given** a valid authenticated request to an inference route, **When** the request body exceeds 10 MB, **Then** the gateway returns HTTP 413 Request Entity Too Large before forwarding to any upstream service.
2. **Given** a request body of exactly 10 MB, **When** sent to an inference route, **Then** the request is allowed through and processed normally (boundary — inclusive limit).
3. **Given** a request body of 10 MB + 1 byte, **When** sent to an inference route, **Then** the gateway returns HTTP 413 immediately.

---

### User Story 2 - Requests Within Limit Pass Through Unaffected (Priority: P2)

An API consumer sends a normal-sized inference request (well under 10 MB). The gateway allows the request through without modification, and the model proxy receives it as expected.

**Why this priority**: Confirms the size limit enforcement does not introduce regressions for legitimate traffic.

**Independent Test**: Can be fully tested by sending a standard chat completion request (~1–5 KB) and confirming a successful 200 response.

**Acceptance Scenarios**:

1. **Given** a valid request with body size under 10 MB, **When** sent to any inference route, **Then** the request reaches the model proxy and a normal response is returned.
2. **Given** a request with no body (e.g., GET health check), **When** processed by the gateway, **Then** no size check interference occurs and the response is normal.

---

### User Story 3 - Consistent Enforcement Across All Inference Routes (Priority: P3)

All inference routes (chat completions, embeddings, batch, streaming) enforce the same 10 MB body size limit without requiring per-route configuration.

**Why this priority**: Inconsistent enforcement creates attack surface. All routes must share the same protection boundary.

**Independent Test**: Can be tested by sending oversized payloads to each inference route type and confirming all return 413.

**Acceptance Scenarios**:

1. **Given** an oversized payload sent to the chat completions route, **When** processed, **Then** 413 is returned.
2. **Given** an oversized payload sent to the embeddings route, **When** processed, **Then** 413 is returned.
3. **Given** an oversized payload sent to the streaming inference route, **When** processed, **Then** 413 is returned before any stream is opened.

---

### Edge Cases

- What happens when a chunked transfer-encoding request streams past the 10 MB total — does the gateway buffer or reject mid-stream?
- What happens when the `Content-Length` header is absent or spoofed — does the gateway measure actual bytes received?
- How does the system behave when multiple simultaneous oversized requests arrive — each must be independently rejected without resource leakage.
- What error body format does the 413 response carry — plain text, JSON, or HTML?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The gateway MUST reject any inference request whose body exceeds 10 MB with HTTP 413 Request Entity Too Large.
- **FR-002**: Rejection MUST occur at the gateway before any part of the request body is forwarded to LiteLLM, Guardrails, or any other internal service.
- **FR-003**: The limit MUST apply uniformly to all inference routes (chat completions, embeddings, streaming, batch).
- **FR-004**: Requests with body size at or below 10 MB MUST NOT be affected — they must pass through the gateway normally.
- **FR-005**: The 413 response MUST include a structured error body indicating the reason for rejection and the configured size limit.
- **FR-006**: The size limit value (10 MB) MUST be externally configurable via environment variable or gateway configuration without requiring a code change or image rebuild.
- **FR-007**: The gateway MUST measure actual received bytes, not rely solely on the `Content-Length` header, to prevent spoofing.
- **FR-008**: The rejection MUST be logged with sufficient metadata (request ID, source IP, route, declared and actual size) for security audit purposes.

### Key Entities

- **Size Limit Policy**: The configured maximum body size (10 MB default) applied at gateway scope; governs all inference routes.
- **Inference Request**: Any HTTP request arriving at Kong destined for `/v1/chat/completions`, `/v1/embeddings`, or other LiteLLM-proxied endpoints.
- **Rejection Response**: The HTTP 413 response returned to the caller; carries structured error payload with limit details.
- **Audit Log Entry**: Metadata record written on each rejection: timestamp, request ID, route, source IP, declared size, actual size.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of requests with body > 10 MB are rejected at the gateway with HTTP 413 — zero such requests reach any upstream service.
- **SC-002**: 0% of requests with body ≤ 10 MB are incorrectly rejected — no false positives for legitimate traffic.
- **SC-003**: Gateway rejection latency for oversized requests is under 50 ms — enforcement adds no meaningful delay to legitimate traffic paths.
- **SC-004**: All inference route types (chat, embeddings, streaming, batch) enforce the limit — verified by independent smoke test for each route.
- **SC-005**: Every rejection produces an audit log entry with all required metadata fields present — observable via the platform logging stack.
- **SC-006**: The size limit can be reconfigured and take effect without redeploying or rebuilding any service.

## Assumptions

- The gateway (Kong) is the single entry point for all inference traffic; no route bypasses Kong to reach LiteLLM directly (enforced since Phase 02).
- The 10 MB limit applies to the raw request body size, not the base64-decoded or decompressed size.
- Non-inference routes (admin API, health checks, Keycloak, Prometheus) are out of scope — the limit applies only to LiteLLM-proxied inference routes.
- Streaming requests are subject to the same total body size limit; partial streaming is not a supported bypass vector.
- The 413 error response body format follows the OpenAI error schema (`{"error": {"message": "...", "type": "...", "code": 413}}`) for client compatibility.
- Rate limiting (Phase 013) and request correlation (Phase 014) remain in place; this feature adds a complementary size enforcement layer.
