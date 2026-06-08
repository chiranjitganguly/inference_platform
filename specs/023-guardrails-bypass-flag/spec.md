# Feature Specification: Guardrails Bypass Flag

**Feature Branch**: `023-guardrails-bypass-flag`

**Created**: 2026-06-08

**Status**: Done

**Input**: Caller needs to opt out of guardrails validation on a per-request basis and receive true SSE streaming (not buffered) when they do so. Without bypass, the guardrails service accumulates the full upstream response for post-inference scanning before forwarding — preventing real-time streaming. Callers with trusted content or latency-sensitive workloads need a way to skip the scanning layer while still routing through the full Kong → Guardrails → LiteLLM chain.

## Clarifications

### Session 2026-06-08

- Q: Does bypass route around the guardrails service entirely? → A: No. The request still enters the guardrails service via Kong. The flag controls what the service does — nothing vs. full validation.
- Q: What is the default? → A: `guardrails: true` (or absent). Omitting the flag preserves all existing behaviour.
- Q: Is the flag forwarded to LiteLLM? → A: No. It is stripped from the request body before the upstream call.
- Q: What happens on streaming bypass? → A: The guardrails service uses `httpx client.stream()` to hold the upstream connection open and forwards SSE chunks immediately as they arrive.
- Q: Does the audit log still fire on bypass? → A: Yes. Every request through the guardrails service generates an audit entry, bypass or not.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Guardrails-off streaming (Priority: P1)

A developer is building a latency-sensitive application and wants to receive inference tokens as they are generated. They set `guardrails: false` and `stream: true`. The guardrails service opens an upstream streaming connection to LiteLLM and forwards each SSE data chunk immediately — no buffering, no post-inference scans. The caller receives the first token within the same round-trip window as a direct LiteLLM call.

**Why this priority**: The primary motivation for the bypass flag is streaming fidelity. Without it, every streaming request is buffered to a single SSE event by the guardrails post-inference scan.

**Independent Test**: Send `POST /v1/chat/completions` with `guardrails: false, stream: true`. Capture SSE lines with timestamps — at least two `data:` lines must arrive at different wall-clock times, proving true streaming.

**Acceptance Scenarios**:

1. **Given** a streaming request with `guardrails: false`, **When** the upstream begins generating tokens, **Then** the caller receives `data:` SSE lines as they are produced — not all at once after generation completes.
2. **Given** `guardrails: false` in the body, **When** the request reaches LiteLLM, **Then** the `guardrails` key is absent from the forwarded body — LiteLLM never sees the flag.
3. **Given** `guardrails: false`, **When** an error chunk is returned by LiteLLM (e.g., model overloaded), **Then** the error SSE frame is forwarded to the caller unchanged; the guardrails service does not swallow it.

---

### User Story 2 — Guardrails-off non-streaming (Priority: P2)

A developer sends a non-streaming request with `guardrails: false`. The guardrails service proxies directly to LiteLLM with no validation, returns the full buffered response, and writes an audit log entry.

**Why this priority**: Completeness — both streaming and non-streaming callers should be able to opt out.

**Independent Test**: Send `POST /v1/chat/completions` with `guardrails: false, stream: false (or absent)`. Verify HTTP 200 and a well-formed completion response, and verify the audit log has an entry with `guardrails_bypass: true`.

---

### User Story 3 — Guardrails-on behaviour unchanged (Priority: P1)

A caller omits `guardrails` (or sets it to `true`). All existing validation gates run as before — vision, function-calling, structured output. Streaming responses are still buffered at the guardrails layer (post-inference scan requirement). No regression.

**Acceptance Scenarios**:

1. **Given** `guardrails` is absent from the request body, **When** the request arrives, **Then** all validation gates execute as if the flag does not exist.
2. **Given** `guardrails: true`, **When** a streaming request arrives, **Then** the response is still buffered (post-inference scan runs); no change from pre-feature behaviour.

---

### Edge Cases

- What if `guardrails` is set to a non-boolean value (e.g., `"yes"`, `1`, `null`)? Python's `bool()` coercion handles this: `bool("yes")` → True, `bool(0)` → False, `bool(null)` → False. Callers should use `true`/`false`.
- What if the request body is not valid JSON? The guardrails extraction block catches the parse exception and defaults to `guardrails: true` (safe default — validate everything).
- What if `guardrails: false` is sent to a non-chat-completions endpoint (e.g., embeddings)? The flag extraction only runs for `POST /v1/chat/completions`. Other paths are unaffected.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST accept an optional `guardrails` boolean field in the `POST /v1/chat/completions` request body.
- **FR-002**: When `guardrails: false`, the guardrails service MUST skip all pre-proxy validation gates (vision, function-calling, structured output) and forward the request directly to LiteLLM.
- **FR-003**: When `guardrails: false` AND `stream: true`, the guardrails service MUST use a true streaming connection to LiteLLM (`httpx client.stream()`) and forward SSE chunks to the caller as they arrive, without buffering the full response.
- **FR-004**: When `guardrails: false` AND `stream: false` (or absent), the guardrails service MUST forward the request to LiteLLM and return the buffered response without running any validation.
- **FR-005**: The `guardrails` field MUST be stripped from the request body before it is forwarded to LiteLLM. LiteLLM must never receive the flag.
- **FR-006**: When `guardrails` is absent or `true`, ALL existing validation behaviour MUST be preserved with zero regression.
- **FR-007**: An audit log entry MUST be written for every request regardless of the `guardrails` flag value.
- **FR-008**: The `guardrails` field is only applicable to `POST /v1/chat/completions`. All other paths (embeddings, etc.) are unaffected.

### Key Entities

- **GuardrailsBypassPath**: The code path in `proxy()` taken when `_guardrails_on = False`. Has two branches: streaming (`_streaming_passthrough`) and non-streaming (buffered direct call).
- **StreamingPassthrough**: An httpx streaming connection that keeps the upstream response open and yields byte chunks to the FastAPI `StreamingResponse` in real time.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With `guardrails: false, stream: true`, at least two SSE `data:` chunks arrive at different wall-clock timestamps (verified by per-chunk timestamp logging).
- **SC-002**: With `guardrails: false`, the `guardrails` key is absent from the body received by LiteLLM (verified via LiteLLM request log or debug proxy).
- **SC-003**: With `guardrails` absent or `true`, all existing validation acceptance tests from features 018–020 pass without modification.
- **SC-004**: Every request through the guardrails service produces an audit log entry, including bypass requests.
- **SC-005**: The `_streaming_passthrough` function correctly cleans up the httpx client and stream context after the response is fully consumed or if the client disconnects mid-stream.

## Assumptions

- The guardrails service is the correct layer to implement bypass — it is the only layer with visibility into both the validation gates and the streaming path.
- Callers who set `guardrails: false` accept responsibility for unscanned content. There is no platform-level enforcement of content policies on the bypass path.
- The `guardrails` flag name does not conflict with any existing LiteLLM or OpenAI API field names.
- Post-inference scanning (toxicity, relevance) is not yet implemented in the guardrails service (it is a future phase). When implemented, it should respect the bypass flag too.
