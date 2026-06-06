# Data Model: Multimodal Image Support

**Branch**: `018-multimodal-image-support` | **Date**: 2026-06-06

## Overview

No new persistent storage is introduced. The data model describes the in-flight request/response structures that the Guardrails validation layer operates on and the in-memory vision capability registry.

---

## 1. Request Structures

### TextContent

A text part within a multimodal message content array.

| Field | Type | Required | Validation |
|---|---|---|---|
| `type` | `"text"` | Yes | Must be literal `"text"` |
| `text` | `string` | Yes | Non-empty |

### ImageUrlContent

An image part within a multimodal message content array.

| Field | Type | Required | Validation |
|---|---|---|---|
| `type` | `"image_url"` | Yes | Must be literal `"image_url"` |
| `image_url.url` | `string` | Yes | Must match `^https://` or `^data:image/[^;]+;base64,`; base64 decoded payload must be ≤ 5 MB |
| `image_url.detail` | `"low" \| "high" \| "auto"` | No | Default: `"auto"` when omitted |

### MultimodalMessage

A chat message that may carry multimodal content.

| Field | Type | Required | Notes |
|---|---|---|---|
| `role` | `"user" \| "assistant" \| "system"` | Yes | Only `"user"` messages may contain image parts |
| `content` | `string \| list[TextContent \| ImageUrlContent]` | Yes | String form = text-only (passthrough, no vision validation needed) |

**Constraints**:
- If `content` is a list, at least one part MUST have `type: "text"`.
- Image parts are only valid in `role: "user"` messages.
- Maximum image parts per request (across all messages): configurable, default `5`.

### ChatCompletionRequest (multimodal extension)

This extends the existing OpenAI-compatible `/v1/chat/completions` body.

| Field | Type | Required | Vision-specific constraint |
|---|---|---|---|
| `model` | `string` | Yes | Must resolve to a vision-capable model when any message contains image parts |
| `messages` | `list[MultimodalMessage]` | Yes | Validated as above when image parts are detected |
| `stream` | `boolean` | No | MUST be `false` or absent when image parts are present |

---

## 2. Validation Error Responses

All errors follow the platform structured error schema (Constitution §4.4).

All error responses use the OpenAI error envelope (per clarification Q2, pending ADR for constitution §4.4):

```json
{"error": {"message": "...", "type": "invalid_request_error", "code": "<code>"}}
```

| Scenario | HTTP Status | `code` |
|---|---|---|
| Non-vision model requested | 400 | `vision_model_required` |
| Streaming with image parts | 400 | `vision_streaming_not_supported` |
| Image count exceeds limit | 400 | `image_count_exceeded` |
| Malformed base64 data URI | 400 | `invalid_image_data_uri` |
| Single image exceeds 5 MB | 413 | `image_too_large` |
| Invalid `detail` value | 400 | `invalid_image_detail` |
| No text part alongside images | 400 | `missing_text_part` |

---

## 3. Vision Capability Registry (in-memory)

Populated on Guardrails service startup by calling LiteLLM's `/model/info` endpoint.

### VisionCapabilityCache

| Field | Type | Notes |
|---|---|---|
| `vision_model_names` | `frozenset[str]` | Set of model names where `"vision" in capabilities` |
| `loaded_at` | `datetime` | Timestamp of last successful load |

**Lifecycle**:
- Populated once on startup via `GET http://litellm:4000/model/info` with `Authorization: Bearer <LITELLM_MASTER_KEY>`
- If the endpoint is unreachable at startup, service MUST fail to start (fail-fast — a guardrails service with no capability registry must not become a silent passthrough)
- Refreshed on SIGHUP

---

## 4. Audit Log Extension

The existing audit entry format gains one new field for vision requests. All other fields remain unchanged.

```json
{
  "timestamp": "...",
  "event_type": "inference_request",
  "request_id": "...",
  "key_hash": "...",
  "model_name": "gpt-4o",
  "pii_entity_count": 0,
  "scanner_blocked": false,
  "image_part_count": 2
}
```

`image_part_count` is `0` for text-only requests. It counts image parts across all messages in the request. The image data itself is never logged.

---

## 5. State Transitions

The vision validation gate sits between request receipt and proxy forwarding.

```
Receive request
       │
       ▼
Has image parts?──No──► existing proxy path (unchanged)
       │
      Yes
       │
       ▼
stream: true? ──Yes──► 400 vision_streaming_not_supported
       │
      No
       │
       ▼
Model in vision_model_names? ──No──► 400 vision_model_required
       │
      Yes
       │
       ▼
Validate each image part ──Invalid──► 400 (specific code)
       │
      Valid
       │
       ▼
image_part_count ≤ MAX_IMAGES? ──No──► 400 image_count_exceeded
       │
      Yes
       │
       ▼
Inject default detail="auto" where omitted
       │
       ▼
Forward to LiteLLM (existing proxy path)
```
