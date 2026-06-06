# ADR-018: OpenAI Error Envelope for Vision Validation Rejections

**Date**: 2026-06-06
**Status**: Accepted
**Feature**: 018-multimodal-image-support
**Amends**: Constitution §4.4 (Error Responses)

---

## Context

Constitution §4.4 mandates a platform-wide structured error schema:

```json
{
  "error": "machine_readable_code",
  "message": "Human readable description",
  "detail": { "field": "additional context" }
}
```

During clarification of feature 018 (multimodal image support), the decision was made that vision-validation rejections on `/v1/chat/completions` should use the OpenAI error envelope instead:

```json
{
  "error": {
    "message": "Human readable description",
    "type": "invalid_request_error",
    "code": "machine_readable_code"
  }
}
```

**Reason**: `/v1/chat/completions` is the primary OpenAI-compatible endpoint. Clients using the OpenAI Python SDK, LangChain, LlamaIndex, and similar OpenAI-compatible libraries parse error responses by inspecting `error.message` and `error.code` on the nested object. A platform-schema error body from this endpoint breaks those clients' error handlers, requiring them to add special-case logic — which contradicts Constitution §III (OpenAI API Compatibility is Mandatory).

Vision-validation rejections (non-vision model, streaming + image, oversized image, malformed URI, etc.) all occur on this endpoint and are exactly the error class that OpenAI SDK clients handle natively via the error envelope.

---

## Decision

**Scope**: The OpenAI error envelope applies exclusively to vision-validation rejection responses from `POST /v1/chat/completions` when the request contains image parts.

**All other platform endpoints and error conditions** continue to use the existing platform error schema (`{"error": "code", "message": "...", "detail": {...}}`). This amendment is narrowly scoped.

### Error codes used by vision validation

| HTTP Status | `error.type` | `error.code` |
|---|---|---|
| 400 | `invalid_request_error` | `vision_model_required` |
| 400 | `invalid_request_error` | `vision_streaming_not_supported` |
| 400 | `invalid_request_error` | `image_count_exceeded` |
| 400 | `invalid_request_error` | `invalid_image_data_uri` |
| 400 | `invalid_request_error` | `invalid_image_detail` |
| 400 | `invalid_request_error` | `missing_text_part` |
| 413 | `invalid_request_error` | `image_too_large` |

### Example

```json
{
  "error": {
    "message": "Model 'command-r-plus' does not support image inputs. Use a vision-capable model: gpt-4o, gpt-4o-mini, gpt-4.1, claude-sonnet, gemini-flash, gemini-pro.",
    "type": "invalid_request_error",
    "code": "vision_model_required"
  }
}
```

---

## Consequences

**Positive**:
- OpenAI SDK clients receive natively parseable error responses from the vision endpoint with no code changes.
- Aligns with Constitution §III (OpenAI compatibility is mandatory).
- Error codes are machine-readable and map directly to OpenAI's error type taxonomy.

**Negative / mitigations**:
- Two error schemas now exist in the platform. Mitigation: scope is clearly bounded to vision-validation rejections on one endpoint; documented here and in `specs/018-multimodal-image-support/data-model.md`.
- Platform operators who write generic error parsers against the platform schema will not parse these errors with their existing tooling. Mitigation: vision validation errors surface in Guardrails logs with the `image_part_count` audit field, so they are traceable without parsing the response body.

---

## Alternatives Considered

1. **Keep platform schema for all errors** — rejected because it breaks OpenAI SDK error handling on the primary inference endpoint.
2. **Hybrid: platform schema with `detail.openai_compatible` sub-key** — rejected as more complex to implement and document than either pure option; callers would still need custom parsing.
3. **Return platform schema from Guardrails, translate to OpenAI envelope at Kong** — rejected; Kong response transformation plugins add latency and complexity for a narrow case.

---

## Amendment to Constitution §4.4

The following sentence is added to Constitution §4.4 after the error schema table:

> **Exception**: Vision-validation rejections from `POST /v1/chat/completions` (when the request contains `image_url` content parts) use the OpenAI error envelope `{"error": {"message": "...", "type": "invalid_request_error", "code": "..."}}` to preserve OpenAI SDK client compatibility. See ADR-018.
