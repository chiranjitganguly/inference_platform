# Feature Specification: Streaming Chat Completions

**Feature Branch**: `006-streaming-sse`

**Created**: 2026-05-28

**Status**: Draft

**Input**: User description: "Build streaming support on the chat completion endpoint so tokens are delivered to the caller as they are generated. Callers requesting streaming receive a Server-Sent Events response where each line is a JSON token delta prefixed with data:. The stream must end with data: [DONE]. The first token must arrive within 2 seconds. Streaming and non-streaming share the same endpoint path differentiated by the stream field in the request body."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Receive a Streaming Chat Response (Priority: P1)

A developer sends a chat request with `stream: true` and receives tokens progressively as the model generates them, rather than waiting for the complete response. Each token delta arrives as a Server-Sent Events line and the stream terminates cleanly with `data: [DONE]`.

**Why this priority**: This is the entire value of the feature — without token-by-token delivery there is no streaming benefit. Everything else in this spec depends on this story working.

**Independent Test**: Send `POST /v1/chat/completions` with `stream: true` and a valid model; verify the response has `Content-Type: text/event-stream`; read all `data:` lines; confirm each parseable line is a valid JSON delta object; confirm the last data line is `[DONE]`; confirm concatenating all `delta.content` values produces a non-empty string.

**Acceptance Scenarios**:

1. **Given** a valid API key and a request body with `stream: true`, `model`, and `messages`, **When** the developer POSTs to `/v1/chat/completions`, **Then** the response status is HTTP 200 with `Content-Type: text/event-stream`.
2. **Given** a streaming response in progress, **When** each chunk arrives, **Then** it is formatted as `data: <json>\n\n` where `<json>` is a valid JSON object containing a `choices` array with at least one element carrying a `delta` object.
3. **Given** the model has finished generating, **When** the final chunk is sent, **Then** the stream ends with the line `data: [DONE]` and the connection closes.
4. **Given** a valid streaming request, **When** the first chunk arrives, **Then** it does so within 2 seconds of the request being accepted.

---

### User Story 2 — Non-Streaming Requests Are Unaffected (Priority: P1)

An existing developer using `stream: false` (or omitting the `stream` field) continues to receive a complete JSON response on the same endpoint path, with no behaviour change after streaming is enabled.

**Why this priority**: Backwards compatibility is non-negotiable — existing callers must not be broken. Both modes share `POST /v1/chat/completions`; the `stream` field is the only differentiator.

**Independent Test**: Send `POST /v1/chat/completions` without a `stream` field; verify HTTP 200 with `Content-Type: application/json`; verify the response body is a complete chat completion object (not a stream); verify `choices[0].message.content` is a non-empty string.

**Acceptance Scenarios**:

1. **Given** a request body with `stream: false` or with `stream` omitted, **When** the developer POSTs to the endpoint, **Then** the response is HTTP 200 with `Content-Type: application/json` and a complete chat completion object — identical to pre-streaming behaviour.
2. **Given** both streaming and non-streaming requests hitting the platform simultaneously, **When** each request is processed, **Then** each receives the correct response format for its `stream` value with no cross-contamination.

---

### User Story 3 — Streaming Requests Respect Authentication (Priority: P1)

A developer who sends a streaming request without a valid platform API key is rejected at the gateway before any model call is made.

**Why this priority**: Authentication is a platform invariant — streaming requests must not bypass Kong key-auth.

**Independent Test**: Send `POST /v1/chat/completions` with `stream: true` and no `Authorization` header; verify HTTP 401 is returned before any streaming response begins.

**Acceptance Scenarios**:

1. **Given** a streaming request with no `Authorization` header, **When** the request reaches Kong, **Then** the response is HTTP 401 and no `text/event-stream` body is sent.
2. **Given** a streaming request with an invalid API key, **When** Kong validates the credential, **Then** the response is HTTP 401.

---

### User Story 4 — Invalid Inputs Return 400 on Streaming Requests (Priority: P2)

A developer who requests streaming with an unknown model name or invalid input receives an HTTP 400 error immediately — not a streaming 200 that later errors mid-stream.

**Why this priority**: Clear, fast failure for invalid inputs applies equally to streaming requests. Receiving a 200 that errors mid-stream breaks client parsers.

**Independent Test**: Send `POST /v1/chat/completions` with `stream: true` and `model: "nonexistent-model-xyz"`; verify HTTP 400 with a structured error body naming the invalid model; verify response time < 50 ms (no upstream call).

**Acceptance Scenarios**:

1. **Given** a streaming request with a model name not in the platform catalogue, **When** the request is processed, **Then** the response is HTTP 400 with a structured error body — not a 200 with a streaming error event.
2. **Given** a streaming request with an empty `messages` array, **When** the request is processed, **Then** the response is HTTP 400.

---

### Edge Cases

- What happens when the upstream LLM provider drops the connection or errors mid-stream? → LiteLLM emits a structured `data: {"error": {"message": "...", "type": "upstream_error"}}\n\n` SSE event then closes the connection. The caller receives a parseable error event rather than a silent truncation.
- What happens when the caller disconnects before the stream completes? → The platform detects the closed connection and stops consuming the upstream stream; no resources are held open unnecessarily.
- What happens when Phoenix or Langfuse is unreachable during a streaming request? → Identical to non-streaming: the stream is returned normally; the observability failure is logged as a warning. Inference is never blocked on sink availability.
- What happens when the upstream provider returns a rate-limit or timeout error during a streaming request? → The platform returns HTTP 502 or 503 with a structured JSON error body — not a partial stream.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The platform MUST accept `stream: true` in the `POST /v1/chat/completions` request body on the same path as non-streaming requests (`/v1/chat/completions`).
- **FR-002**: When `stream: true`, the response MUST have HTTP status 200 and `Content-Type: text/event-stream`.
- **FR-003**: Each streaming chunk MUST be formatted as `data: <json>\n\n` where `<json>` is a valid, independently parseable JSON object with `id`, `object: "chat.completion.chunk"`, `model`, and a `choices` array. Each choice MUST carry a `delta` object with `role` (first chunk only) and/or `content` (subsequent chunks). Every chunk MUST be parseable in isolation without requiring prior chunk context.
- **FR-004**: The `finish_reason` field in each choice MUST be `null` for all intermediate chunks; for the final content chunk it MUST be one of `stop`, `length`, or `content_filter`.
- **FR-005**: The stream MUST terminate with the sentinel line `data: [DONE]\n\n` followed by connection close.
- **FR-006**: The first chunk MUST arrive at the caller within 2 seconds of the platform accepting the request (p95).
- **FR-007**: When `stream: false` or `stream` is omitted, the response MUST be identical to the non-streaming behaviour defined in feature 005 — a complete JSON document with `Content-Type: application/json`. No behaviour change is introduced for existing non-streaming callers.
- **FR-008**: A streaming request with an unrecognised model name MUST return HTTP 400 with a structured error body before any stream begins.
- **FR-009**: A streaming request with an empty `messages` array MUST return HTTP 400.
- **FR-010**: A streaming request without a valid platform API key MUST be rejected with HTTP 401 at Kong before reaching the inference service.
- **FR-011**: Streaming requests MUST produce a single observability span in Phoenix Arize and a trace in Langfuse. The Phoenix span MUST close when the stream ends (connection close or `[DONE]` sentinel). Token counts are aggregated at stream completion and emitted once when the span closes. If Phoenix or Langfuse is unreachable, the stream is returned normally and the failure is logged as a warning.
- **FR-012**: The `X-Request-ID`, `X-Platform: inference-platform`, and `X-API-Version: 1` response headers MUST be present on streaming responses.
- **FR-013**: Streaming responses MUST NOT be written to the platform response cache. The cache MUST be bypassed entirely for any request where `stream: true`; cached responses from prior non-streaming requests MUST NOT be served as streaming responses.

### Key Entities

- **StreamingRequest**: Same schema as `ChatRequest` (feature 005) with `stream: true`. No additional fields required.
- **StreamChunk**: A single Server-Sent Events event. Fields: `id` (string), `object` (string: `"chat.completion.chunk"`), `model` (string), `choices` (array of `StreamChoice`).
- **StreamChoice**: Fields: `index` (integer: 0), `delta` (object with optional `role` string and optional `content` string), `finish_reason` (null or `"stop"` | `"length"` | `"content_filter"`).
- **StreamSentinel**: The literal string `[DONE]` sent as the final `data:` value to signal stream end.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The first token of a streaming response arrives at the caller within 2 seconds (p95) of the request being accepted — measured at the Kong gateway.
- **SC-002**: 100% of completed streaming requests produce a Phoenix Arize span and a Langfuse trace with correct token counts — verified by querying both sinks after each streaming smoke test run.
- **SC-003**: Existing non-streaming contract tests (`tests/contract/test_chat_completions.py`) continue to pass without modification after streaming is enabled — zero regressions.
- **SC-004**: Invalid model names return HTTP 400 within 50 ms (p99) on streaming requests — no upstream call is made.
- **SC-005**: `make smoke` includes a streaming probe that verifies: HTTP 200, `Content-Type: text/event-stream`, at least one `data:` chunk received, and `data: [DONE]` as the final line.

## Clarifications

### Session 2026-05-28

- Q: What activates streaming mode? → A: `stream: true` in the request body; no other mechanism.
- Q: Must streaming responses be written to the response cache? → A: No — streaming responses must never be written to the response cache; cache is bypassed entirely for `stream: true` requests.
- Q: Must each SSE chunk be independently parseable? → A: Yes — each chunk must be valid, independently parseable JSON without requiring prior chunk context.
- Q: How is Phoenix observability handled for streaming? → A: A single span is opened per streaming request; the span closes when the stream ends (on `[DONE]` or connection close); token counts are emitted once at span close.

## Assumptions

- LiteLLM v1.52 proxies streaming natively — when `stream: true` is sent to LiteLLM, it forwards the upstream provider's SSE response to the caller verbatim. No custom streaming middleware is required.
- The `arize_phoenix` and `langfuse` callbacks in LiteLLM v1.52 handle streaming completion events; token counts are aggregated at stream end and emitted once, not per chunk.
- Kong 3.6 in proxy mode does not buffer SSE responses — chunks pass through immediately without accumulation.
- Streaming is enabled per-request via the `stream` field; no platform-level configuration toggle is needed.
- The Guardrails service (future feature) will need to handle streaming pass-through when introduced; the same ordered exception documented in feature 005 applies here.
- The platform does not maintain session state; callers supply the full conversation history in `messages` for both streaming and non-streaming requests.
- SSE retry and reconnection on disconnect are the caller's responsibility; the platform does not implement server-side reconnection.
- The smoke test streaming probe uses `curl --no-buffer` to read the SSE stream; measuring first-chunk latency is done with `curl -w '%{time_starttransfer}'`.
