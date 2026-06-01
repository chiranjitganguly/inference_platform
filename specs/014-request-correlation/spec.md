# Feature Specification: Request Correlation

**Feature Branch**: `014-request-correlation`

**Created**: 2026-05-31

**Status**: Draft

**Input**: User description: "Build request correlation so every request through the gateway is assigned a unique identifier that travels through all downstream services enabling end-to-end tracing. The gateway must forward W3C traceparent and tracestate headers to downstream services so Phoenix Arize can correlate gateway activity with LLM spans. The unique request ID must be returned to the caller in a response header. Client-provided IDs must be replaced by gateway-generated ones."

---

## Clarifications

### Session 2026-05-31

- Q: What format should the correlation identifier take? → A: UUID (standard universally unique identifier format)
- Q: How are client-provided `X-Request-ID` headers handled? → A: Overwritten unconditionally at the gateway — no client value is ever passed downstream
- Q: Is the correlation ID echoed back to the caller even when forwarded to upstream services? → A: Yes, the gateway returns the ID in the response header on every response regardless of upstream outcome
- Q: How are W3C `traceparent` and `tracestate` headers propagated to downstream services? → A: Forwarded without modification by the gateway so downstream services receive the complete trace context
- Q: How does the distributed tracing backend link gateway activity to LLM spans? → A: The OTel Collector enriches spans with the `X-Request-ID` value as a dedicated trace attribute, enabling cross-signal correlation
- Q: If a downstream service fails to receive or propagate the correlation header, does the gateway still return the UUID? → A: Yes — the gateway always returns `X-Request-ID` in the response header regardless of downstream propagation failures; UUID emission is decoupled from upstream outcome

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Debug a Failed Inference Request End-to-End (Priority: P1)

A platform operator receives a user complaint about a failed or slow inference call. Using the request ID returned in the response header, the operator searches across all observability systems — gateway logs, LLM spans, guardrails events — and finds every correlated record in under a minute without needing to guess at timestamps or model names.

**Why this priority**: End-to-end traceability is the primary user value. Without a shared correlation identifier, debugging cross-service failures is manual and error-prone. This is the core scenario that justifies the feature.

**Independent Test**: Make a single inference request through the gateway, record the `X-Request-ID` response header value, then query the gateway access log, guardrails audit log, and Phoenix Arize trace list — all three must return records sharing that exact UUID.

**Acceptance Scenarios**:

1. **Given** a client sends a valid inference request, **When** the gateway responds, **Then** the response includes an `X-Request-ID` header containing a unique, gateway-generated UUID.
2. **Given** a request has been processed, **When** an operator queries gateway logs by the returned request ID, **Then** all log lines for that request — including upstream forwarding events — are returned.
3. **Given** a request has been processed, **When** an operator queries Phoenix Arize for the same request ID, **Then** the LLM span for that request is found and linked to the gateway-level trace via the shared UUID attribute.

---

### User Story 2 — Prevent Clients from Injecting Their Own Trace IDs (Priority: P2)

A client sends a request containing a custom `X-Request-ID` or `traceparent` header in an attempt to influence trace correlation. The gateway unconditionally overwrites those headers with its own authoritative identifiers, so all observability data is based solely on gateway-issued IDs.

**Why this priority**: Allowing client-controlled IDs would corrupt the trust model of the tracing system — operators would not be able to tell whether a trace ID is genuine or forged. Unconditional overwrite is a security and integrity requirement.

**Independent Test**: Send a request with a manually set `X-Request-ID` and `traceparent` header. Verify the response `X-Request-ID` is a gateway-generated UUID that differs from the client-supplied value, and that Phoenix Arize records only the gateway-issued trace context.

**Acceptance Scenarios**:

1. **Given** a client sends a request with a custom `X-Request-ID` header, **When** the gateway processes it, **Then** the response `X-Request-ID` is a new gateway-generated UUID, not the client's value.
2. **Given** a client sends a request with a custom `traceparent` header, **When** the gateway forwards the request to downstream services, **Then** the forwarded `traceparent` is gateway-generated and the client's value is absent from all downstream requests.
3. **Given** a client sends both `X-Request-ID` and `traceparent` with any values, **When** the gateway processes the request, **Then** both headers are overwritten unconditionally — no partial preservation of client values occurs.

---

### User Story 3 — Correlate Guardrails and LiteLLM Spans to a Single Request (Priority: P2)

A security reviewer investigates a request flagged by the guardrails service for PII. Using the shared request ID, they pull the guardrails scan record, the LiteLLM routing decision, and the Phoenix LLM span all at once, confirming the full lifecycle of the flagged call without cross-referencing timestamps.

**Why this priority**: The gateway, guardrails, and LiteLLM are three separate services in the request chain. Correlation across all three is needed for incident investigation and compliance auditing.

**Independent Test**: Send a request that triggers a guardrails PII-redaction event. Confirm the same UUID appears in the guardrails audit log, the LiteLLM callback emission, and the Phoenix trace — all three, from a single ID lookup.

**Acceptance Scenarios**:

1. **Given** a request passes through guardrails, **When** guardrails writes its audit entry, **Then** the entry includes the same UUID that was returned to the caller.
2. **Given** a request reaches LiteLLM, **When** LiteLLM emits a callback to Phoenix Arize, **Then** the W3C `traceparent` header received by LiteLLM carries the gateway-generated trace context so Phoenix links the LLM span to the gateway span.
3. **Given** a trace span is ingested by the OTel Collector, **When** the span is stored, **Then** the span includes the `X-Request-ID` UUID as a dedicated trace attribute queryable independently of the `traceparent`.

---

### User Story 4 — Identify All Requests from a Specific Consumer in a Time Window (Priority: P3)

An operator wants to audit all requests made by consumer `team-alpha` in the past hour. Each request has its own unique UUID, so the operator can retrieve the full ordered list of calls and drill into any individual one.

**Why this priority**: Per-consumer auditability depends on each request having a unique, stable ID. This is a secondary use case — correlation is the enabler; consumer-scoped audit is the benefit.

**Independent Test**: Send five requests authenticated as consumer `team-alpha`. Query the gateway logs filtering by that consumer identity. Verify five distinct UUIDs are returned, each resolvable to a full trace.

**Acceptance Scenarios**:

1. **Given** multiple requests are made by the same consumer, **When** logs are filtered by consumer identity, **Then** each request appears as a distinct entry with its own unique UUID.
2. **Given** a specific UUID from the list, **When** used to query the full trace, **Then** all service-level events for that request are returned.

---

### Edge Cases

- What happens when a request times out mid-flight — does the UUID still appear in all partial records written before the timeout?
- How does the system handle concurrent requests from the same consumer arriving within milliseconds — are UUIDs guaranteed to be distinct?
- If a downstream service (guardrails, LiteLLM) fails to receive or propagate the correlation header, the gateway still returns the UUID to the caller and records it in its own access log — UUID emission is independent of downstream propagation success.
- Client-supplied `X-Request-ID` and `traceparent` headers are always overwritten unconditionally — there is no scenario where a client value is preserved or forwarded.
- What happens to `tracestate` headers sent by clients — they are stripped and replaced with gateway-generated context; no client `tracestate` values are forwarded.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The gateway MUST generate a UUID as the correlation identifier for every inbound request before any forwarding occurs.
- **FR-002**: The gateway MUST unconditionally overwrite any `X-Request-ID`, `traceparent`, or `tracestate` headers supplied by the client — no client-provided value for these headers is ever forwarded to downstream services.
- **FR-003**: The gateway MUST return the UUID correlation identifier to the caller in an `X-Request-ID` response header on every response — including error responses and cases where a downstream service is unreachable or fails to propagate the header. UUID emission is decoupled from upstream outcome.
- **FR-004**: The gateway MUST propagate the UUID correlation identifier to all downstream services (guardrails, LiteLLM) as the `X-Request-ID` request header.
- **FR-005**: The gateway MUST generate a W3C-compliant `traceparent` header and forward it without modification to all downstream services so distributed tracing backends can link spans across the request chain.
- **FR-006**: The gateway MUST forward the W3C `tracestate` header to downstream services when a tracestate value exists; if no tracestate context is generated, the header MUST be omitted rather than forwarded empty.
- **FR-007**: UUIDs MUST be unique across all concurrent and sequential requests — no two requests may share the same UUID within any rolling 24-hour window.
- **FR-008**: The UUID correlation identifier MUST be included in every gateway access log entry and every structured audit log entry written during that request's lifecycle.
- **FR-009**: The system MUST NOT expose any prompt content, model response content, or PII in the UUID or any header derived from it.
- **FR-010**: The observability collector MUST enrich every ingested span with the `X-Request-ID` UUID as a dedicated, queryable trace attribute, enabling span lookup by request ID independently of the W3C trace ID.

### Key Entities

- **Correlation ID**: A UUID generated by the gateway at request ingress. Travels as `X-Request-ID` through the full service chain, is returned to the caller in the response header, and is stored as a trace attribute on every observability span for the request.
- **W3C Trace Context**: A pair of headers — `traceparent` (version, trace ID, parent span ID, flags) and optionally `tracestate` (vendor-specific key-value pairs) — that enable distributed tracing systems to link spans across service boundaries per the W3C Trace Context specification.
- **Gateway Span**: The top-level observability unit created at the gateway for a single inbound request, anchoring the distributed trace. Carries the UUID as a trace attribute.
- **LLM Span**: The child span created by Phoenix Arize when LiteLLM processes the request, linked to the gateway span via the propagated `traceparent` and independently queryable via the UUID trace attribute.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every response from the gateway — success or error — includes an `X-Request-ID` header containing a valid UUID. Zero responses without this header across 1,000 consecutive test requests.
- **SC-002**: For 100% of processed requests, the UUID in the gateway access log matches the value returned to the caller.
- **SC-003**: For 100% of processed requests, a Phoenix Arize trace record is discoverable by the UUID trace attribute within 30 seconds of the request completing.
- **SC-004**: Zero client-supplied `X-Request-ID` or `traceparent` values appear in any downstream service request — verified by replaying 50 requests with crafted client headers and inspecting downstream service logs.
- **SC-005**: An operator can retrieve all log and trace records for any single request using only the `X-Request-ID` UUID returned in the response header, without needing additional identifiers.
- **SC-006**: No measurable increase in end-to-end request latency attributable to UUID generation — verified by comparing p99 latency before and after feature activation over 500 requests.

---

## Assumptions

- The gateway is the sole entry point for all client traffic, as established by the platform architecture — no client can reach guardrails or LiteLLM directly.
- The W3C Trace Context specification (version `00`) is the target format for `traceparent` headers; no proprietary tracing formats are required.
- Phoenix Arize is already configured to ingest traces via the OTel endpoint and will automatically correlate child spans to a parent trace when it receives a valid `traceparent`.
- Guardrails and LiteLLM services already emit structured log entries that can be extended to include the forwarded `X-Request-ID` header without service restarts.
- The UUID does not need to be human-readable or carry semantic meaning (e.g., no consumer ID or timestamp embedded in the identifier).
- Log storage and querying infrastructure is already operational and indexed on request metadata fields — no new log pipeline work is required.
- W3C `tracestate` propagation is in scope only for the gateway-to-downstream leg; client `tracestate` values are always discarded and overwritten.
- The OTel Collector is already deployed and receiving spans from all services in the request chain — no new collector deployment is required by this feature.
