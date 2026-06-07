# Quickstart: Function Calling Support

**Branch**: `019-function-calling`

## Prerequisites

- `make up-core` (brings up Kong, LiteLLM, Guardrails, Redis, Postgres)
- `make seed-kong` (Kong routes configured)
- `PLATFORM_KEY` set to a valid Kong consumer API key

## Send your first function-calling request

### Auto tool selection

```bash
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "tool_choice": "auto",
    "tools": [{
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {"type": "string"}
          },
          "required": ["city"]
        }
      }
    }],
    "messages": [{"role": "user", "content": "What is the weather in Tokyo?"}]
  }' | jq '.choices[0].message.tool_calls[0]'
```

Expected output:
```json
{
  "id": "call_...",
  "type": "function",
  "function": {
    "name": "get_weather",
    "arguments": "{\"city\": \"Tokyo\"}"
  }
}
```

### Verify arguments are valid JSON

```bash
curl -s ... | jq '.choices[0].message.tool_calls[0].function.arguments | fromjson'
```

### Verify finish_reason is "tool_calls"

```bash
curl -s ... | jq '.choices[0].finish_reason'
# Expected: "tool_calls"
```

## Verify non-function-capable model is rejected

```bash
curl -s -w "\nHTTP %{http_code}" -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-haiku",
    "tools": [{"type": "function", "function": {"name": "test", "parameters": {}}}],
    "messages": [{"role": "user", "content": "test"}]
  }'
# Expected: HTTP 400, error.code == "function_calling_model_required"
```

## Verify streaming + tools is rejected

```bash
curl -s -w "\nHTTP %{http_code}" -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "stream": true,
    "tools": [{"type": "function", "function": {"name": "test", "parameters": {}}}],
    "messages": [{"role": "user", "content": "test"}]
  }'
# Expected: HTTP 400, error.code == "function_calling_streaming_not_supported"
```

## Run smoke tests

```bash
make smoke
```

All existing probes plus the new `[019]` function-calling cases must pass.

## Function-capable models

| Model | Provider |
|---|---|
| `gpt-4o` | OpenAI |
| `gpt-4o-mini` | OpenAI |
| `gpt-4.1` | OpenAI |
| `o4-mini` | OpenAI |
| `claude-sonnet` | Anthropic |
| `gemini-pro` | Google |
| `gemini-flash` | Google |
| `command-r-plus` | Cohere |

## Known limitations (v1)

- `stream: true` is not supported with `tools`. Use non-streaming mode.
- Streaming tool call deltas are out of scope.
- `tool_call_id` uniqueness and multi-turn state management are the caller's responsibility.
