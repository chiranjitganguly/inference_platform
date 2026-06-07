# Research: Function Calling Support

**Branch**: `019-function-calling` | **Date**: 2026-06-06

## Findings

---

### Decision 1: Function-calling capability declaration

**Decision**: Use the existing `capabilities: ["function-calling"]` list in `model_info` within `services/litellm/config.yaml`. The spec phrase `supports_functions: true` maps to `"function-calling" in model_info.capabilities`.

**Rationale**: Identical convention to vision (`supports_vision: true` ↔ `"vision" in capabilities`). Already established in the codebase; no new metadata field needed.

**Alternatives considered**: Adding a separate `supports_functions: true` scalar. Rejected: redundant with the capabilities list, creates two sources of truth.

---

### Decision 2: Current function-calling capability state in config

**Decision**: `function-calling` is already declared in `capabilities` for the correct models. No `config.yaml` changes required.

| Model | Has `function-calling`? | Notes |
|---|---|---|
| `gpt-4o` | Yes | User-confirmed; in config ✓ |
| `gpt-4o-mini` | Yes | User-confirmed; in config ✓ |
| `gpt-4.1` | Yes | In config ✓ |
| `o4-mini` | Yes | In config ✓ |
| `claude-sonnet` | Yes | User-confirmed; in config ✓ |
| `gemini-pro` | Yes | In config ✓ |
| `gemini-flash` | Yes | In config ✓ |
| `command-r-plus` | Yes | In config ✓ |
| `claude-haiku` | **No** | Config has only `chat, streaming` |
| `text-embedding-*` | **No** | Embedding models; no chat |

---

### Decision 3: Function-calling capability registry in Guardrails

**Decision**: Create `FunctionCallingCapabilityCache` in `services/guardrails/function_calling.py` — mirrors `VisionCapabilityCache` exactly, loading from `GET /model/info` and extracting names where `"function-calling" in capabilities`. Separate class (not merged with vision cache) to preserve independent lifecycle and avoid coupling.

**Rationale**: The vision pattern is proven and reviewed. Replicating it keeps the codebase consistent and makes the two gates independently testable. A future consolidation into `ModelCapabilityCache` is deferred.

**Alternatives considered**: Merging both caches into a single `ModelCapabilityCache`. Rejected for this feature: increases scope and risk without immediate value. Worth revisiting after both gates are stable.

---

### Decision 4: Request-side validation responsibilities

**Decision**: `validate_function_calling_request()` in `function_calling.py` enforces, in order:

1. `stream: true` + non-empty `tools` → 400 `function_calling_streaming_not_supported`
2. Model without `"function-calling"` in capabilities → 400 `function_calling_model_required`
3. `tools` empty array with `tool_choice` explicitly set → 400 `tool_choice_requires_tools`
4. `tool_choice` names a function not present in `tools` → 400 `tool_choice_function_not_found`
5. Any tool definition missing `name` or `parameters` not a dict → 400 `invalid_tool_definition`

All rejections use the OpenAI error envelope per ADR-018: `{"error": {"message": "...", "type": "invalid_request_error", "code": "..."}}`.

---

### Decision 5: Response-side validation (FR-016) — new behaviour vs. vision

**Decision**: After receiving the upstream response for a function-calling request, `validate_tool_call_response()` in `function_calling.py` inspects the response body:

- If `choices[0].message.tool_calls` is present: attempt `json.loads()` on each `function.arguments` value.
- If any `arguments` fails JSON parsing: return 502 `invalid_tool_arguments` — never forward malformed arguments to the caller.
- If the JSON body cannot be parsed at all (provider returned non-JSON): pass through as-is (provider-level error, not a function-calling-specific issue).

This is the key difference from vision: guardrails must buffer and inspect the response body for function-calling requests, not just the request.

**Rationale**: FR-004 and the clarification guarantee callers always receive parseable `arguments`. Passing malformed JSON silently would cause hard-to-debug failures in clients that `JSON.parse()` arguments without error handling. The gateway is the correct enforcement point.

---

### Decision 6: Integration point in `main.py`

**Decision**: Add two checkpoints to `proxy()` in `main.py`:

1. **Pre-proxy** (after existing vision gate): if `has_tools(body)` → call `validate_function_calling_request()`. Return error response immediately on failure.
2. **Post-proxy** (after receiving upstream response, before returning to caller): if the request had tools and upstream returned HTTP 200 → call `validate_tool_call_response()`. Return 502 if arguments are malformed.

The pre-proxy check uses the `FunctionCallingCapabilityCache` from `app.state.fc_cache`. The post-proxy check requires buffering the full response body (already done for non-streaming responses in the current proxy).

---

### Decision 7: Phoenix tool_call span attributes

**Decision**: Function-calling responses are traced through LiteLLM's existing `arize_phoenix` callback. The callback naturally propagates `tool_calls` metadata (count, names) from the provider response to the span. The guardrails layer does not need to write custom span attributes. Argument values do not appear in spans because LiteLLM's callback does not log response content per the no-persistence invariant.

**Rationale**: Keeping observability in the LiteLLM callback layer respects the architecture's single-responsibility boundary. Custom span injection in guardrails would duplicate what LiteLLM already does and add coupling.

---

### Decision 8: `tools` + streaming — scope exclusion

**Decision**: Requests with `stream: true` and a non-empty `tools` array are rejected at the guardrails pre-proxy gate with HTTP 400 `function_calling_streaming_not_supported` (OpenAI envelope, per clarification Q3 / ADR-018 consistency).

This is the same approach as vision streaming exclusion (`vision_streaming_not_supported`). Streaming tool calls are a provider-specific protocol extension (SSE deltas with partial `arguments`) that requires separate implementation work.
