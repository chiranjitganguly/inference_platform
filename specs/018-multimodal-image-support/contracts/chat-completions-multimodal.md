# Contract: Chat Completions — Multimodal Extension

**Endpoint**: `POST /v1/chat/completions`
**Branch**: `018-multimodal-image-support` | **Date**: 2026-06-06

This document describes the **multimodal extension** to the existing `/v1/chat/completions` contract. All existing text-only behaviour is unchanged. This extension adds support for image parts in the `messages[].content` array.

---

## Request

### Content-Type

`application/json`

### Body — multimodal variant

```json
{
  "model": "gpt-4o",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "What is in this image?"
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "https://example.com/photo.jpg",
            "detail": "auto"
          }
        }
      ]
    }
  ]
}
```

### Body — base64 image variant

```json
{
  "model": "gpt-4o",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "Describe this chart."
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA...",
            "detail": "high"
          }
        }
      ]
    }
  ]
}
```

### Field constraints (multimodal-specific)

| Field | Constraint |
|---|---|
| `model` | Must be a vision-capable model (`"vision" in capabilities`). Allowed: `gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `claude-sonnet`, `gemini-flash`, `gemini-pro`. |
| `messages[].content` | When an array, must contain at least one `type: "text"` part. |
| `image_url.url` | Must begin with `https://` or `data:image/<mime-type>;base64,`. HTTP (non-TLS) URLs are rejected. |
| `image_url.detail` | Optional. One of `"low"`, `"high"`, `"auto"`. Defaults to `"auto"` when omitted. |
| Image count | Maximum 5 image parts across all messages per request (configurable via `MAX_IMAGES_PER_REQUEST`). |
| `stream` | Must be `false` or absent when any image part is present. |

---

## Response — success

HTTP 200. Identical to the text-only chat completion response shape.

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1749200000,
  "model": "gpt-4o",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "The image shows a bar chart comparing..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 1042,
    "completion_tokens": 87,
    "total_tokens": 1129
  }
}
```

Response headers (attached by Kong, unchanged):
- `X-Request-ID`
- `X-Platform: inference-platform`
- `X-API-Version: 1`

---

## Response — error cases

Vision-validation rejections use the **OpenAI error envelope** (per ADR-018, amending constitution §4.4 for this endpoint). All other platform errors retain the platform schema.

```json
{
  "error": {
    "message": "Human readable description.",
    "type": "invalid_request_error",
    "code": "<machine_readable_code>"
  }
}
```

### 400 — Non-vision model

```json
{
  "error": {
    "message": "Model 'command-r-plus' does not support image inputs. Use a vision-capable model: claude-sonnet, gemini-flash, gemini-pro, gpt-4.1, gpt-4o, gpt-4o-mini.",
    "type": "invalid_request_error",
    "code": "vision_model_required"
  }
}
```

### 400 — Streaming with image parts

```json
{
  "error": {
    "message": "Streaming is not supported for multimodal requests. Set 'stream' to false or omit it.",
    "type": "invalid_request_error",
    "code": "vision_streaming_not_supported"
  }
}
```

### 400 — Image count exceeded

```json
{
  "error": {
    "message": "Request contains 7 images; maximum allowed is 5.",
    "type": "invalid_request_error",
    "code": "image_count_exceeded"
  }
}
```

### 400 — Invalid image data URI

```json
{
  "error": {
    "message": "Image part at index 1 has a malformed data URI. Expected format: data:image/<jpeg|png|gif|webp>;base64,<data>.",
    "type": "invalid_request_error",
    "code": "invalid_image_data_uri"
  }
}
```

### 413 — Image too large

```json
{
  "error": {
    "message": "Image part at index 0 exceeds the 5 MB limit (6.2 MB).",
    "type": "invalid_request_error",
    "code": "image_too_large"
  }
}
```

### 400 — Missing text part

```json
{
  "error": {
    "message": "Multimodal messages must contain at least one text part alongside image parts.",
    "type": "invalid_request_error",
    "code": "missing_text_part"
  }
}
```

---

## Backward compatibility

- Requests with `messages[].content` as a plain `string` continue to work unchanged.
- Requests with no image parts in the content array continue to work unchanged.
- No new endpoint is introduced; this is a content-level extension of the existing `/v1/chat/completions` contract.
- Clients using the OpenAI Python SDK's multimodal helpers send exactly the format above; no client code changes are required.
