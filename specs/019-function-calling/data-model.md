# Data Model: Function Calling Support

**Branch**: `019-function-calling` | **Date**: 2026-06-06

## Overview

No new persistent storage is introduced. The data model describes the in-flight request/response structures that the Guardrails validation layer operates on, and the in-memory function-calling capability registry.

---

## 1. Request Structures

### ToolDefinition

A single function a caller makes available to the model.

| Field | Type | Required | Validation |
|---|---|---|---|
| `type` | `"function"` | Yes | Must be literal `"function"` |
| `function.name` | `string` | Yes | Non-empty; unique within the `tools` array |
| `function.description` | `string` | No | Improves model routing accuracy; no validation |
| `function.parameters` | `object` | Yes | Must be a JSON object (dict); JSON Schema format |

### ToolChoice

Controls whether and which tool the model may invoke.

| Value | Type | Semantics |
|---|---|---|
| `"auto"` | string | Model decides; may invoke a tool or respond with text |
| `"none"` | string | Model MUST NOT invoke any tool; text-only response |
| `"required"` | string | Model MUST invoke at least one tool (if supported by provider) |
| `{"type": "function", "function": {"name": "<name>"}}` | object | Model MUST invoke the named function |

### ChatCompletionRequest (function-calling extension)

Extends the existing `/v1/chat/completions` request body.

| Field | Type | Required | Function-calling constraint |
|---|---|---|---|
| `model` | `string` | Yes | Must resolve to a function-capable model when `tools` is non-empty |
| `tools` | `list[ToolDefinition]` | No | When present and non-empty, triggers function-calling validation |
| `tool_choice` | `ToolChoice` | No | Default: `"auto"` when `tools` is non-empty and `tool_choice` is absent |
| `stream` | `boolean` | No | MUST be `false` or absent when `tools` is non-empty |

---

## 2. Response Structures

### ToolCall

An entry in the model's `tool_calls` array signalling a function to invoke.

| Field | Type | Constraint |
|---|---|---|
| `id` | `string` | Unique identifier for this call; used in follow-up `role: "tool"` messages |
| `type` | `"function"` | Always `"function"` in this release |
| `function.name` | `string` | Name of the function to invoke |
| `function.arguments` | `string` | **MUST be a valid, JSON-parseable string** — enforced by Guardrails post-proxy (FR-016) |

### ChatCompletionResponse (function-calling)

When the model selects a tool, the response differs from a text-only response:

| Field | Expected value | Notes |
|---|---|---|
| `choices[0].finish_reason` | `"tool_calls"` | MUST be this value when `tool_calls` is present |
| `choices[0].message.role` | `"assistant"` | Unchanged |
| `choices[0].message.content` | `null` or `""` | Provider may return null when tool_calls is set |
| `choices[0].message.tool_calls` | `list[ToolCall]` | One or more entries; parallel calls are supported |

---

## 3. Validation Error Responses

All validation rejections use the **OpenAI error envelope** (per ADR-018, consistent with vision rejections on this endpoint):

```json
{"error": {"message": "...", "type": "invalid_request_error", "code": "<code>"}}
```

| Scenario | HTTP Status | `code` |
|---|---|---|
| `stream: true` + non-empty `tools` | 400 | `function_calling_streaming_not_supported` |
| Model without `function-calling` capability | 400 | `function_calling_model_required` |
| `tools` is empty array but `tool_choice` is set | 400 | `tool_choice_requires_tools` |
| `tool_choice` names function not in `tools` | 400 | `tool_choice_function_not_found` |
| Tool definition missing `name` or `parameters` not a dict | 400 | `invalid_tool_definition` |
| Provider returns unparseable `function.arguments` | 502 | `invalid_tool_arguments` |

The 502 case uses `"type": "upstream_error"` instead of `"invalid_request_error"` to signal that the error originates from the provider, not from the caller's request.

---

## 4. Function-Calling Capability Registry (in-memory)

Mirrors `VisionCapabilityCache`. Populated on Guardrails service startup.

### FunctionCallingCapabilityCache

| Field | Type | Notes |
|---|---|---|
| `fc_model_names` | `frozenset[str]` | Model names where `"function-calling" in capabilities` |
| Loaded at | startup | Via `GET http://litellm:4000/model/info`; fail-fast if unreachable |

---

## 5. Audit Log Extension

The existing audit entry format gains one new field for function-calling requests.

```json
{
  "timestamp": "...",
  "event_type": "inference_request",
  "request_id": "...",
  "key_hash": "...",
  "model_name": "gpt-4o",
  "pii_entity_count": 0,
  "scanner_blocked": false,
  "image_part_count": 0,
  "tool_count": 2
}
```

`tool_count` is the number of tool definitions in the `tools` array (0 for non-function-calling requests). Function definitions, tool names, and argument values are never logged.

---

## 6. State Transitions

The function-calling validation gate runs alongside (and after) the vision gate.

```
Receive request
       │
       ▼
Has image parts? ──Yes──► vision gate (existing)
       │
      No/pass
       │
       ▼
Has non-empty tools array?──No──► existing proxy path (unchanged)
       │
      Yes
       │
       ▼
stream: true? ──Yes──► 400 function_calling_streaming_not_supported
       │
      No
       │
       ▼
Model in fc_model_names? ──No──► 400 function_calling_model_required
       │
      Yes
       │
       ▼
tool_choice consistency check ──Invalid──► 400 (specific code)
       │
      Valid
       │
       ▼
Tool definition structure check ──Invalid──► 400 invalid_tool_definition
       │
      Valid
       │
       ▼
Forward to LiteLLM (existing proxy path)
       │
       ▼
Receive upstream response
       │
       ▼
Response has tool_calls? ──No──► return to caller unchanged
       │
      Yes
       │
       ▼
Each arguments valid JSON? ──No──► 502 invalid_tool_arguments
       │
      Yes
       │
       ▼
Return response to caller
```
