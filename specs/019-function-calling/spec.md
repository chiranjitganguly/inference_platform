# Feature Specification: Function Calling Support

**Feature Branch**: `019-function-calling`

**Created**: 2026-06-06

**Status**: Clarified

**Input**: User description: "Build function calling support so the model can signal which tool to invoke based on the user's intent, returning structured tool call information. Callers define one or more functions with JSON Schema parameter definitions. The model must choose the right function automatically when tool_choice is auto."

## Clarifications

### Session 2026-06-06

- Q: How does the platform surface function calling to callers? → A: Through the existing OpenAI-compatible `/v1/chat/completions` endpoint — callers pass a `tools` array and an optional `tool_choice` field exactly as they would with the OpenAI API.
- Q: How is function-calling capability declared on a model? → A: `supports_functions: true` in model metadata; only models carrying this flag may accept the `tools` parameter.
- Q: Must `function.arguments` be valid JSON? → A: Yes — `function.arguments` in every tool call entry in the response MUST be a valid, JSON-parseable string.
- Q: What is the exact `finish_reason` when the model selects a function? → A: `finish_reason` MUST be `"tool_calls"` — no other value is permitted when a tool call is present.
- Q: How are function calls recorded in Phoenix? → A: Phoenix Arize LLM spans MUST include `tool_call` event attributes — specifically the count of tool calls and the function names invoked. Argument values MUST NOT appear in any span attribute (constitution §II / FR-014).
- Q: What error format should non-function-capable model rejections use? → A: OpenAI error envelope — `{"error": {"message": "...", "type": "invalid_request_error", "code": "function_calling_model_required"}}` — consistent with ADR-018 for all `/v1/chat/completions` validation rejections.
- Q: What happens when a provider returns `function.arguments` that is not valid JSON? → A: The gateway MUST return HTTP 502 with `{"error": {"message": "...", "type": "upstream_error", "code": "invalid_tool_arguments"}}` — never pass malformed arguments to the caller.
- Q: What error format for `stream: true` + `tools` rejection? → A: OpenAI envelope — `{"error": {"message": "...", "type": "invalid_request_error", "code": "function_calling_streaming_not_supported"}}` — consistent with all guardrails rejections on `/v1/chat/completions`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Model Selects and Invokes a Tool Automatically (Priority: P1)

A caller submits a chat completion request with a `tools` array containing one or more function definitions and sets `tool_choice: "auto"`. Based on the user's message, the model determines which function (if any) best satisfies the intent and returns a structured tool call in the response. The caller reads the tool name and arguments, executes the function externally, and optionally continues the conversation by submitting the result.

**Why this priority**: This is the core use case. Without automatic tool selection, function calling has no value. It is the most common integration pattern for AI agents, search augmentation, and structured data extraction.

**Independent Test**: Can be fully tested by sending a request with a weather-lookup function definition and a message asking about the weather — confirming the response contains a tool call with the correct function name and well-formed arguments, rather than a plain text answer.

**Acceptance Scenarios**:

1. **Given** a caller sends a chat completion request with a `tools` array defining a `get_weather` function and `tool_choice: "auto"`, and the user message asks "What is the weather in Paris?", **When** the request is processed, **Then** the response contains `finish_reason: "tool_calls"` and `choices[0].message.tool_calls[0].function.name == "get_weather"` with valid JSON arguments matching the function's schema.
2. **Given** the same setup but the user message is a general knowledge question unrelated to weather, **When** the request is processed, **Then** the response contains a plain text answer and `finish_reason: "stop"` (no tool call generated).
3. **Given** the caller submits the tool result back as a message with `role: "tool"`, **When** the follow-up request is processed, **Then** the model produces a natural language answer incorporating the tool result.

---

### User Story 2 - Caller Forces a Specific Tool (Priority: P2)

A caller sets `tool_choice` to a specific function name, requiring the model to always call that function regardless of the message content. This is used when the caller knows in advance which function is needed (e.g., a structured data extraction pipeline).

**Why this priority**: Forced tool selection enables deterministic extraction workflows. Important for production pipelines where the caller controls routing.

**Independent Test**: Can be fully tested by sending a request with `tool_choice: {"type": "function", "function": {"name": "extract_invoice"}}` and a plain text invoice — confirming the response always contains a tool call for `extract_invoice`.

**Acceptance Scenarios**:

1. **Given** a caller sends a request with `tool_choice: {"type": "function", "function": {"name": "extract_invoice"}}` and an invoice as the message, **When** the request is processed, **Then** `choices[0].message.tool_calls[0].function.name == "extract_invoice"` regardless of message content.
2. **Given** the caller forces a function name that is not in the `tools` array, **When** the request is processed, **Then** the response is HTTP 400 with a descriptive error indicating the requested function is not defined.

---

### User Story 3 - Caller Disables Tool Use Explicitly (Priority: P2)

A caller passes a `tools` array but sets `tool_choice: "none"` to prevent the model from invoking any tool. The model responds with plain text only, even when the message would otherwise trigger a tool call.

**Why this priority**: Some workflows supply function definitions for context but want text-only responses for certain turns (e.g., summarisation after tool results are collected).

**Independent Test**: Can be fully tested by sending a weather-question request with a weather tool defined but `tool_choice: "none"` — confirming the response is plain text with `finish_reason: "stop"`.

**Acceptance Scenarios**:

1. **Given** a caller sends a request with a tool defined and `tool_choice: "none"`, and a message that would normally trigger a tool call, **When** the request is processed, **Then** the response is plain text with `finish_reason: "stop"` and no `tool_calls` field.

---

### User Story 4 - Multiple Functions Defined, Model Selects the Right One (Priority: P3)

A caller provides several function definitions covering different capabilities (e.g., weather, calendar, calculator). The model selects the single most appropriate function for the user's message without being told which one to use.

**Why this priority**: Multi-tool disambiguation demonstrates the routing intelligence of the model and enables agent-style applications. Builds on US1.

**Independent Test**: Can be fully tested by defining three unrelated functions and sending a message clearly matching one — confirming only that function appears in the tool call, not the others.

**Acceptance Scenarios**:

1. **Given** a caller provides three function definitions (`get_weather`, `book_meeting`, `calculate`) and the message is "Book a meeting with Alice tomorrow at 3pm", **When** the request is processed, **Then** `choices[0].message.tool_calls[0].function.name == "book_meeting"` and no other tool calls are present.
2. **Given** the same three tools and a message that does not clearly match any tool, **When** `tool_choice` is `"auto"`, **Then** the model responds with plain text (no tool call).

---

### Edge Cases

- What happens when `tools` is an empty array?
- What happens when a function's JSON Schema `parameters` object is malformed or missing required fields?
- If the provider returns `function.arguments` that is not valid JSON, the gateway returns HTTP 502 `invalid_tool_arguments` (see FR-016); the caller never receives malformed arguments.
- What happens when `tool_choice` names a specific function but the `tools` array is empty?
- How does the platform handle a request where `tools` is defined but no `tool_choice` is provided (default behaviour)?
- What happens if the model generates multiple tool calls simultaneously (parallel tool calls)?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The chat completion endpoint MUST accept an optional `tools` array in the request body, where each entry defines a function with a `name`, optional `description`, and a `parameters` object following the JSON Schema specification.
- **FR-002**: The chat completion endpoint MUST accept an optional `tool_choice` field with the values `"auto"`, `"none"`, or `{"type": "function", "function": {"name": "<name>"}}`.
- **FR-003**: When `tool_choice` is `"auto"` (or absent when `tools` is provided), the model MUST autonomously decide whether to invoke a tool based on the user's message; it MUST return either a tool call or a plain text response.
- **FR-004**: When the model decides to call a tool, the response MUST include `finish_reason: "tool_calls"` and a `tool_calls` array in `choices[0].message`, where each entry contains a unique `id`, `type: "function"`, and a `function` object with `name` (string) and `arguments` (a valid, JSON-parseable string — never a bare value or malformed JSON).
- **FR-005**: When `tool_choice` is set to a specific function name, the model MUST always produce a tool call for that function regardless of message content.
- **FR-006**: When `tool_choice` is `"none"`, the model MUST NOT produce any tool calls; the response MUST be plain text with `finish_reason: "stop"`.
- **FR-007**: The system MUST reject requests where `tool_choice` names a specific function that does not appear in the `tools` array, returning a 400-level error.
- **FR-008**: The system MUST reject requests where a function definition in `tools` is missing a required field (`name`) or has a `parameters` value that is not a valid JSON object, returning a 400-level error.
- **FR-009**: When a caller submits a follow-up message with `role: "tool"` and a `tool_call_id` referencing a prior tool call, the model MUST incorporate the tool result into its next response.
- **FR-010**: The system MUST maintain a registry of models that declare function-calling capability via `supports_functions: true` in their metadata; only those models may be selected for requests containing a non-empty `tools` array. Requests targeting a model without `supports_functions: true` MUST be rejected with HTTP 400 using the OpenAI error envelope (`{"error": {"message": "...", "type": "invalid_request_error", "code": "function_calling_model_required"}}`) before reaching LiteLLM.
- **FR-011**: Function calling MUST work end-to-end through the existing request chain (Kong → Guardrails → LiteLLM → provider) without bypassing any layer.
- **FR-012**: The guardrails pipeline MUST pass `tools` and `tool_choice` fields through to LiteLLM unchanged; no content scanning is applied to function definitions.
- **FR-013**: All function calling requests MUST be traced end-to-end with the same observability signals as standard chat completions (correlation ID, OTel spans, audit log entry). Phoenix Arize LLM spans for function-calling responses MUST include `tool_call` event attributes: the count of tool calls returned and the function names invoked. Argument values MUST NOT appear in any span attribute (see FR-014).
- **FR-014**: `tool_calls` content in responses MUST NOT be persisted in any log, trace attribute, or audit entry (function argument values may contain sensitive data).
- **FR-015**: When `tools` is absent or empty and `tool_choice` is also absent, the request MUST behave identically to a standard chat completion (no regression).
- **FR-016**: The guardrails service MUST inspect the upstream response for any function-calling request and verify that every `tool_calls[*].function.arguments` value is a valid JSON-parseable string. If any entry fails this check, the gateway MUST return HTTP 502 with `{"error": {"message": "Upstream model returned malformed tool call arguments.", "type": "upstream_error", "code": "invalid_tool_arguments"}}` rather than forwarding the malformed response to the caller.

### Key Entities

- **Tool Definition**: A single function a caller makes available to the model. Key attributes: `name` (unique within the request), `description` (optional, improves model selection accuracy), `parameters` (JSON Schema object describing accepted arguments).
- **Function-Capable Model**: A model entry in the model catalogue that declares `supports_functions: true` in its metadata. Only these models may receive requests containing a non-empty `tools` array.
- **Tool Call**: The model's signal that a specific function should be invoked. Key attributes: `id` (unique call identifier for multi-turn tracking), `type` (always `"function"` in this release), `function.name`, `function.arguments` (JSON string).
- **Tool Result Message**: A follow-up message from the caller containing the output of an invoked function. Key attributes: `role: "tool"`, `tool_call_id` (references the originating tool call `id`), `content` (the function's return value as a string).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A caller providing a function definition and a matching user message receives a structured tool call response — not plain text — 100% of the time when `tool_choice` is set to that function's name.
- **SC-002**: When `tool_choice` is `"auto"`, the model correctly routes to a tool call (vs. plain text) for messages that clearly match a defined function, with routing accuracy indistinguishable from direct provider behaviour.
- **SC-003**: Requests containing malformed tool definitions or invalid `tool_choice` values receive a structured 400-level error within the same latency envelope as other validation rejections.
- **SC-004**: All existing text-only and multimodal chat completion requests continue to return HTTP 200 with correct response shapes after function calling is deployed (zero regressions).
- **SC-005**: No function argument values appear in any audit log, trace export, or metrics label.
- **SC-006**: The smoke-test suite passes with the new function-calling probe cases alongside all pre-existing cases.

## Assumptions

- The OpenAI function calling wire format (`tools` array, `tool_choice` field, `tool_calls` in the response) is the accepted interface; no proprietary function-calling schema will be introduced.
- The default behaviour when `tools` is provided but `tool_choice` is absent is equivalent to `tool_choice: "auto"`.
- Parallel tool calls (the model returning multiple `tool_calls` entries in a single response) are supported if the underlying provider returns them; the gateway passes them through without modification.
- Streaming function calling (`stream: true` with `tools`) is out of scope for this initial release; requests combining `stream: true` with a non-empty `tools` array are rejected with HTTP 400 using the OpenAI envelope (`code: "function_calling_streaming_not_supported"`) — consistent with ADR-018.
- The guardrails service passes `tools` and `tool_choice` fields through to LiteLLM without inspection; deep validation of JSON Schema correctness beyond field presence is delegated to the upstream provider.
- Function-calling capability is gated by `supports_functions: true` in model metadata (parallel to vision's `supports_vision: true`). The guardrails service enforces this gate before forwarding to LiteLLM; requests targeting a non-function-capable model are rejected at the gateway.
- `tool_call_id` uniqueness and multi-turn conversation state management are the caller's responsibility; the platform is stateless.
