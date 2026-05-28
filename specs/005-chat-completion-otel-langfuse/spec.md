# Feature Specification: Chat Completion with Observability

**Feature Branch**: `005-chat-completion-otel-langfuse`

**Created**: 2026-05-28

**Status**: Draft

**Input**: User description: "Build a chat completion endpoint that accepts a model identifier and a conversation message array and returns a complete AI-generated response. Every request must emit an OpenTelemetry span with LLM-specific attributes — token counts, model name, latency — routed automatically to Phoenix Arize. Every request must be logged as a Langfuse trace linked to the prompt version used when metadata.prompt_name is provided. The response must include generated text, token counts, and finish reason. Invalid model names return 400. Metadata containing team and request_id must tag the request for traceability in both Phoenix and Langfuse."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Submit a Chat Completion Request (Priority: P1)

An application developer sends a conversation to the platform and receives a complete AI-generated reply with usage statistics.

**Why this priority**: The core value delivery — without this nothing else matters.

**Independent Test**: POST a valid request with a known model name and a single user message; verify the response contains `content` (non-empty string), `prompt_tokens` and `completion_tokens` (positive integers), and `finish_reason` (non-empty string).

**Acceptance Scenarios**:

1. **Given** a valid API key and a request body with `model`, `messages` (one or more), **When** the developer POSTs to the chat endpoint, **Then** the response is HTTP 200 with `content`, `prompt_tokens`, `completion_tokens`, and `finish_reason` fields.
2. **Given** a valid request, **When** the response is received, **Then** `finish_reason` is one of `stop`, `length`, or `content_filter`.
3. **Given** `stream` is omitted from the request, **When** the response arrives, **Then** it is a complete JSON document (not a streaming event stream) — streaming defaults to false.

---

### User Story 2 — Traceability via Phoenix Arize (Priority: P1)

A platform operator needs to inspect every inference request's latency, token usage, and model name in Phoenix Arize for quality monitoring, without any extra instrumentation effort from the calling application.

**Why this priority**: Observability is a non-negotiable platform guarantee — every request must produce a span, not just those where the developer remembers to instrument.

**Independent Test**: After a successful chat completion, query Phoenix Arize for a span matching the `X-Request-ID` header; verify the span carries `llm.token_count.prompt`, `llm.token_count.completion`, and `llm.model_name` attributes with correct values.

**Acceptance Scenarios**:

1. **Given** any successful chat completion, **When** the platform emits the response, **Then** a span exists in Phoenix Arize containing `llm.model_name` equal to the requested model name.
2. **Given** any successful chat completion, **When** the platform emits the response, **Then** the span in Phoenix carries `llm.token_count.prompt` and `llm.token_count.completion` matching the values in the API response.
3. **Given** a request with `metadata.team` and `metadata.request_id`, **When** the span is emitted, **Then** those values appear as tags on the span, enabling team-level filtering in Phoenix.

---

### User Story 3 — Prompt-Linked Langfuse Trace (Priority: P1)

A data scientist wants every inference request linked to the exact prompt version that produced it, so evaluation scores can be attributed to specific prompt iterations.

**Why this priority**: Prompt governance is a constitution-level requirement — production traces must be linkable to prompt versions to enable promotion/rollback decisions.

**Independent Test**: Send a request with `metadata.prompt_name: "system-v1"`. Query Langfuse for the resulting trace; verify the trace references the `production` version of prompt `system-v1` and carries `team` and `request_id` metadata tags.

**Acceptance Scenarios**:

1. **Given** a request with `metadata.prompt_name` set, **When** the request completes, **Then** a Langfuse trace exists linked to the `production` version of that named prompt.
2. **Given** a request without `metadata.prompt_name`, **When** the request completes, **Then** a Langfuse trace still exists (unlinked to a specific prompt version) — prompt linking is conditional, trace creation is unconditional.
3. **Given** `metadata.team` and `metadata.request_id` are present, **When** the Langfuse trace is created, **Then** both appear as trace metadata tags.

---

### User Story 4 — Reject Invalid Model Names (Priority: P1)

A developer who accidentally sends an unsupported model name receives an immediate, actionable error rather than a delayed timeout or opaque failure.

**Why this priority**: Fast, clear validation prevents wasted quota and debugging time.

**Independent Test**: POST a request with `model: "nonexistent-model-xyz"`; verify the response is HTTP 400 with a structured error body containing a `message` field that names the invalid model.

**Acceptance Scenarios**:

1. **Given** a model name not in the platform catalogue, **When** the developer submits a chat request, **Then** the response is HTTP 400 with `{"error": "invalid_model", "message": "Model 'X' is not available..."}`.
2. **Given** an empty `model` field, **When** the developer submits a chat request, **Then** the response is HTTP 400.
3. **Given** a valid model name with `status: unavailable` in the catalogue, **When** the developer submits a chat request, **Then** the response is HTTP 503 (not 400 — the model exists but is currently offline).

---

### Edge Cases

- What happens when `messages` is an empty array? → HTTP 400, messages must contain at least one entry.
- What happens when the upstream LLM provider returns an error (rate limit, timeout)? → Fallback chain activates per constitution; if all fallbacks exhausted, HTTP 503.
- What happens when `metadata.prompt_name` references a prompt with no `production` version? → Request proceeds without prompt linking; Langfuse trace created with a warning tag `prompt_not_found: true`.
- What happens when `metadata` is omitted entirely? → Request proceeds normally; Phoenix and Langfuse traces are created without team/request_id tags.
- What happens when Phoenix or Langfuse is unreachable at request time? → The chat completion still returns HTTP 200 to the caller; the observability failure is recorded as a warning in the platform audit log. Inference is never blocked on observability sink availability.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The platform MUST expose a `POST /v1/chat/completions` endpoint accepting `model` (string, required), `messages` (array, minimum 1 element, required), `stream` (boolean, optional, default `false`), and `metadata` (object, optional).
- **FR-002**: The response MUST include `content` (the generated text), `prompt_tokens` (integer), `completion_tokens` (integer), and `finish_reason` (string: `stop` | `length` | `content_filter`).
- **FR-003**: A request with an unrecognised `model` value MUST return HTTP 400 with a structured error body naming the invalid model. A model with `status: unavailable` MUST return HTTP 503.
- **FR-004**: A request with an empty `messages` array MUST return HTTP 400.
- **FR-005**: Every completed request MUST produce a span in Phoenix Arize carrying `llm.model_name`, `llm.token_count.prompt`, and `llm.token_count.completion` as OpenInference attributes. No custom instrumentation code is required from callers — the platform emits spans automatically via the observability callback.
- **FR-006**: Every completed request MUST produce a trace in Langfuse. When `metadata.prompt_name` is provided, the trace MUST be linked to the current `production` version of that named prompt. When `metadata.prompt_name` is absent, the trace is created without prompt linkage.
- **FR-007**: When `metadata.team` and `metadata.request_id` are present, both MUST appear as tags on the Phoenix span and as metadata fields on the Langfuse trace.
- **FR-008**: The `stream` field defaults to `false`; non-streaming responses return a complete JSON document. Streaming responses (when `stream: true`) are out of scope for this feature.
- **FR-009**: Requests without a valid platform API key MUST be rejected with HTTP 401 at the gateway before reaching the chat service.
- **FR-011**: If Phoenix or Langfuse is unreachable when a request completes, the chat response MUST still be returned to the caller with HTTP 200. The observability failure MUST be recorded as a warning-level entry in the platform audit log; it MUST NOT cause the inference request to fail.
- **FR-010**: The response MUST include `X-Request-ID`, `X-Platform: inference-platform`, and `X-API-Version: 1` headers.

### Key Entities

- **ChatRequest**: Input to the endpoint. Fields: `model` (string), `messages` (array of `{role, content}`), `stream` (boolean, default false), `metadata` (object: `team`, `request_id`, `prompt_name` — all optional).
- **ChatResponse**: Output of the endpoint. Fields: `content` (string), `prompt_tokens` (integer), `completion_tokens` (integer), `finish_reason` (string).
- **ObservabilitySpan**: A record in Phoenix Arize carrying OpenInference LLM attributes, created automatically per request.
- **LangfuseTrace**: A record in Langfuse linked optionally to a named prompt version, created automatically per request.
- **PromptVersion**: A versioned system prompt stored in Langfuse; linked by `prompt_name` at the `production` stage label.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Developers receive a complete chat response in under 5 seconds (p95) for standard models under normal load — measured end-to-end at the gateway.
- **SC-002**: 100% of completed chat requests produce a corresponding span in Phoenix Arize with all three required OpenInference attributes present — verified by querying Phoenix after each smoke test run.
- **SC-003**: 100% of completed chat requests produce a Langfuse trace; when `metadata.prompt_name` is provided, 100% of those traces are linked to the prompt's production version — verified by Langfuse API query post-smoke-test.
- **SC-004**: Invalid model names receive HTTP 400 within 50 ms (p99) — no upstream provider call is made.
- **SC-005**: `metadata.team` and `metadata.request_id` are recoverable from both Phoenix spans and Langfuse traces for 100% of requests that include them — verified by end-to-end traceability test.
- **SC-006**: The endpoint is covered by `make smoke` and returns HTTP 200 for a valid test request within the script's timeout.

## Assumptions

- The endpoint is `POST /v1/chat/completions` — the OpenAI-compatible path already routed by Kong in the platform.
- Spans are emitted to Phoenix Arize automatically via the `arize_phoenix` callback already configured in `services/litellm/config.yaml`; no instrumentation code in this feature's scope.
- Langfuse traces are created automatically via the `langfuse` callback already configured in `services/litellm/config.yaml`; prompt linking via `metadata.prompt_name` is achieved by passing the name through LiteLLM's metadata forwarding.
- Streaming (`stream: true`) is out of scope; only non-streaming completions are addressed here.
- The `production` Langfuse prompt version is the label used at runtime; if no prompt with that label exists, the trace proceeds without prompt linkage.
- Auth (Kong key-auth), rate limiting, and PII redaction (Guardrails) are handled by upstream layers per the platform's request chain — not reimplemented here.
- `finish_reason` values are sourced directly from the upstream LLM provider and normalised to `stop | length | content_filter`.

## Clarifications

### Session 2026-05-28

- Q: How are OTel spans routed to Phoenix Arize? → A: Via the `arize_phoenix` LiteLLM callback — no custom instrumentation code required.
- Q: Which OpenInference attributes must the span carry? → A: `llm.token_count.prompt`, `llm.token_count.completion`, `llm.model_name`.
- Q: How is the Langfuse trace linked to a prompt version? → A: Via the `langfuse` LiteLLM callback; `metadata.prompt_name` resolves to the current `production` version of that prompt in Langfuse.
- Q: What is the minimum valid `messages` array size? → A: At least one message required; empty array returns HTTP 400.
- Q: What does an invalid model name return? → A: HTTP 400 with a structured error body naming the invalid model.
- Q: What is the default value of `stream`? → A: `false` — only non-streaming completions are in scope for this feature.
- Q: What happens if Phoenix or Langfuse is unreachable during a request? → A: Degrade gracefully — chat returns HTTP 200; observability failure is logged as a warning in Loki; inference is never blocked on sink availability.
