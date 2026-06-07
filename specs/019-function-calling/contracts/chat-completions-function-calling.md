# Contract: Chat Completions — Function Calling Extension

**Endpoint**: `POST /v1/chat/completions`
**Branch**: `019-function-calling` | **Date**: 2026-06-06

This document describes the **function calling extension** to the existing `/v1/chat/completions` contract. All existing text-only and multimodal behaviour is unchanged.

---

## Request

### Content-Type

`application/json`

### Body — auto tool selection

```json
{
  "model": "gpt-4o",
  "tool_choice": "auto",
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {"type": "string", "description": "City name"},
            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
          },
          "required": ["city"]
        }
      }
    }
  ],
  "messages": [
    {"role": "user", "content": "What is the weather in Paris?"}
  ]
}
```

### Body — forced tool selection

```json
{
  "model": "gpt-4o",
  "tool_choice": {"type": "function", "function": {"name": "extract_invoice"}},
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "extract_invoice",
        "description": "Extract structured data from invoice text",
        "parameters": {
          "type": "object",
          "properties": {
            "vendor": {"type": "string"},
            "total": {"type": "number"},
            "date": {"type": "string", "format": "date"}
          },
          "required": ["vendor", "total"]
        }
      }
    }
  ],
  "messages": [
    {"role": "user", "content": "Invoice from Acme Corp, $1,250.00, dated 2026-05-15"}
  ]
}
```

### Body — tool result follow-up

```json
{
  "model": "gpt-4o",
  "tools": [...],
  "messages": [
    {"role": "user", "content": "What is the weather in Paris?"},
    {
      "role": "assistant",
      "tool_calls": [
        {
          "id": "call_abc123",
          "type": "function",
          "function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\", \"unit\": \"celsius\"}"}
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_abc123",
      "content": "{\"temperature\": 22, \"condition\": \"sunny\"}"
    }
  ]
}
```

### Field constraints (function-calling-specific)

| Field | Constraint |
|---|---|
| `model` | Must declare `function-calling` capability (`"function-calling" in capabilities`). |
| `tools` | When non-empty, each entry must have `type: "function"`, `function.name` (non-empty string), and `function.parameters` (JSON object). |
| `tool_choice` | When set to an object, the named function must exist in `tools`. Cannot be set if `tools` is empty or absent. |
| `stream` | Must be `false` or absent when `tools` is non-empty. |

---

## Response — tool call selected

HTTP 200. The model chose to invoke a function.

```json
{
  "id": "chatcmpl-xyz789",
  "object": "chat.completion",
  "created": 1749200000,
  "model": "gpt-4o",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_abc123",
            "type": "function",
            "function": {
              "name": "get_weather",
              "arguments": "{\"city\": \"Paris\", \"unit\": \"celsius\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ],
  "usage": {
    "prompt_tokens": 87,
    "completion_tokens": 18,
    "total_tokens": 105
  }
}
```

**Guaranteed by guardrails**: `finish_reason` is `"tool_calls"` and every `function.arguments` value is a valid JSON-parseable string.

## Response — no tool selected (auto)

HTTP 200. The model chose plain text.

```json
{
  "choices": [
    {
      "message": {"role": "assistant", "content": "The Eiffel Tower is 330 metres tall."},
      "finish_reason": "stop"
    }
  ]
}
```

---

## Response — error cases

All errors use the OpenAI error envelope (per ADR-018).

### 400 — Non-function-capable model

```json
{
  "error": {
    "message": "Model 'claude-haiku' does not support function calling. Use a function-capable model: claude-sonnet, command-r-plus, gemini-flash, gemini-pro, gpt-4.1, gpt-4o, gpt-4o-mini, o4-mini.",
    "type": "invalid_request_error",
    "code": "function_calling_model_required"
  }
}
```

### 400 — Streaming with tools

```json
{
  "error": {
    "message": "Streaming is not supported for function calling requests. Set 'stream' to false or omit it.",
    "type": "invalid_request_error",
    "code": "function_calling_streaming_not_supported"
  }
}
```

### 400 — tool_choice names unknown function

```json
{
  "error": {
    "message": "tool_choice references function 'nonexistent_fn' which is not defined in the tools array.",
    "type": "invalid_request_error",
    "code": "tool_choice_function_not_found"
  }
}
```

### 400 — Invalid tool definition

```json
{
  "error": {
    "message": "Tool at index 1 is missing required field 'name'.",
    "type": "invalid_request_error",
    "code": "invalid_tool_definition"
  }
}
```

### 502 — Provider returned unparseable arguments

```json
{
  "error": {
    "message": "Upstream model returned malformed tool call arguments that could not be parsed as JSON.",
    "type": "upstream_error",
    "code": "invalid_tool_arguments"
  }
}
```

---

## Backward compatibility

- Requests with no `tools` field (or `tools: []`) and no `tool_choice` continue to work unchanged.
- All existing text-only and multimodal requests are unaffected.
- Clients using the OpenAI Python SDK's function-calling helpers send exactly the format above; no client code changes are required.
