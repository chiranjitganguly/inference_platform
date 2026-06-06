# Implementation Plan: Multimodal Image Support

**Branch**: `018-multimodal-image-support` | **Date**: 2026-06-06 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/018-multimodal-image-support/spec.md`

## Summary

Extend the `/v1/chat/completions` endpoint to accept image parts (`type: image_url`) alongside text in the `messages[].content` array. Vision capability is gated by `"vision" in model_info.capabilities` in `services/litellm/config.yaml`. The Guardrails service validates vision requests before proxying to LiteLLM. LiteLLM handles provider-specific format translation natively. No new services or ports are required.

## Technical Context

**Language/Version**: Python 3.11 (Guardrails service); YAML (LiteLLM config)

**Primary Dependencies**: FastAPI, httpx (async), pydantic v2 (request validation), opentelemetry-sdk — all already present in the guardrails service

**Storage**: None (in-memory vision capability cache populated from LiteLLM `/model/info` at startup)

**Testing**: pytest (smoke tests via `scripts/smoke-test.sh`; contract tests via `tests/contract/`)

**Target Platform**: Linux container (arm64 + amd64), Docker Compose `safety` profile

**Performance Goals**: Multimodal validation overhead ≤ 5 ms p95 (Guardrails pre-proxy gate); end-to-end latency ≤ 2× text-only baseline for the same model (SC-001)

**Constraints**: Image data MUST NOT be logged or traced (Constitution §II); LiteLLM port 4000 is internal-only (Constitution §I); no new Docker services

**Scale/Scope**: Shares the existing per-consumer rate limits (RPM/RPS) set in Kong; image count capped at 5 per request by default

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| **I — Request Flow Integrity** | PASS | No bypass introduced. Client → Kong → Guardrails → LiteLLM chain unchanged. Validation happens inside Guardrails, not before Kong. |
| **II — Prompt Content Ephemeral** | PASS | Image data (URL strings, base64 payloads) is never written to Loki, Phoenix spans, Langfuse, or PostgreSQL. Audit entry logs only `image_part_count` (integer). |
| **III — OpenAI Compatibility** | PASS | Uses the standard OpenAI multimodal `content` array format. No new fields introduced. Existing text-only requests unchanged. |
| **IV — Defence in Depth** | PASS | Kong enforces request size (existing `request-size-limiting` plugin handles large base64 bodies). OPA model-level ABAC is unchanged. Guardrails adds the vision model gate at the content layer. |
| **V — Falsifiable Acceptance Criteria** | PASS | All acceptance criteria are expressible as `curl` commands with expected HTTP status + response body assertions (see quickstart.md). |

*Post-Phase 1 re-check*: Guardrails adds a startup call to LiteLLM `/model/info`. This call is internal (Docker network) and is part of the Guardrails→LiteLLM communication, not a new external dependency. Constitution §I is still satisfied.

## Project Structure

### Documentation (this feature)

```text
specs/018-multimodal-image-support/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── contracts/
│   └── chat-completions-multimodal.md   # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code Changes

```text
services/
├── litellm/
│   └── config.yaml            # MODIFY: add vision to gemini-flash + gpt-4o-mini;
│                              #         update fallback chains for vision models
└── guardrails/
    ├── main.py                # MODIFY: add vision validation pre-proxy gate
    └── vision.py              # NEW: VisionCapabilityCache + request validation logic

scripts/
└── smoke-test.sh              # MODIFY: add multimodal smoke test cases

tests/
└── contract/
    └── test_multimodal.py     # NEW: contract tests for multimodal request validation
```

**Structure Decision**: Single-service modification. No new Docker services. Guardrails vision logic extracted to a sibling module (`vision.py`) to keep `main.py` focused on routing concerns.

## Phase 0: Research

Complete. See [research.md](research.md) for all decisions.

Key resolved decisions:
1. Vision capability = `"vision" in model_info.capabilities` (existing convention)
2. `gemini-flash` and `gpt-4o-mini` need `vision` added to config
3. Guardrails fetches vision model list from LiteLLM `/model/info` at startup (single source of truth)
4. Fallback chains updated: vision models fall back only to other vision models
5. `detail` field default = `"auto"` when omitted
6. Streaming + image parts → immediate 400 rejection
7. Phoenix image token counts flow through LiteLLM's existing `arize_phoenix` callback — no custom span attributes needed in Guardrails

## Phase 1: Design & Contracts

Complete. See linked artifacts below.

### Design decisions

#### `services/litellm/config.yaml` changes

1. Add `vision` to `capabilities` for `gemini-flash` and `gpt-4o-mini`.
2. Update fallback chains so vision-capable primary models only fall back to other vision-capable models:
   - `gpt-4o-mini: [gemini-flash, claude-sonnet]` (was `[claude-haiku, gemini-flash]`)
   - `gemini-flash: [gpt-4o-mini, gemini-pro]` (was `[gpt-4o-mini, claude-haiku]`)
   - All other vision-model fallback chains are already vision-only; no change needed.

#### `services/guardrails/vision.py` (new module)

Responsibilities:
- `VisionCapabilityCache`: Async startup loader that calls `GET /model/info`, extracts model names where `"vision" in capabilities`, and stores as `frozenset[str]`. Raises `RuntimeError` if LiteLLM is unreachable.
- `has_image_parts(messages)`: Returns `True` if any message has a content array containing `type: "image_url"`.
- `validate_vision_request(request_body, vision_models, max_images)`: Runs all validation rules in the order defined in `data-model.md` (state transition diagram). Returns `None` on success or a `VisionValidationError` with the structured error body.
- `inject_detail_defaults(messages)`: Walks all image parts and sets `detail = "auto"` where `detail` is absent.

#### `services/guardrails/main.py` changes

In the `proxy()` function, before forwarding the request body:

```python
if path == "v1/chat/completions" and request.method == "POST":
    body = await _validate_vision(body, request)
    # _validate_vision returns the (possibly detail-injected) body on success,
    # or raises HTTPException(400) with structured error JSON.
```

The `_validate_vision` helper uses the `VisionCapabilityCache` singleton and calls `validate_vision_request`. If validation passes, it returns the body with `detail` defaults injected.

#### Audit log

`_write_audit` gains a new field: `"image_part_count": int`. Zero for text-only requests. Never logs image content — only the count.

#### `tests/contract/test_multimodal.py`

Contract tests covering:
- URL image → 200 with non-empty response content
- Base64 image → 200 with non-empty response content
- Non-vision model → 400 `vision_model_required`
- `stream: true` with image → 400 `vision_streaming_not_supported`
- Malformed base64 URI → 400 `invalid_image_data_uri`
- Image count exceeded → 400 `image_count_exceeded`
- Missing text part → 400 `missing_text_part`
- Text-only request → 200 (regression guard)

#### `scripts/smoke-test.sh` additions

Two new smoke test cases appended to the existing script:
1. Vision URL test: `curl -s -o /dev/null -w "%{http_code}" -X POST :8080/v1/chat/completions` with a real public image URL and `gpt-4o`.
2. Non-vision rejection test: same request but with `model: command-r-plus` → expect 400.

### Artifacts

- [research.md](research.md)
- [data-model.md](data-model.md)
- [contracts/chat-completions-multimodal.md](contracts/chat-completions-multimodal.md)
- [quickstart.md](quickstart.md)

## Complexity Tracking

No constitution violations. No complexity justification required.

---

## Acceptance Criteria (falsifiable)

### AC-1: URL image returns text description

```bash
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -d '{
    "model": "gpt-4o",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "What colour is the sky in this image?"},
        {"type": "image_url", "image_url": {"url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/14/Gatto_europeo4.jpg/320px-Gatto_europeo4.jpg"}}
      ]
    }]
  }'
```

**Expected**: HTTP 200, `choices[0].message.content` is a non-empty string.

---

### AC-2: Base64 image accepted

```bash
IMAGE_B64=$(base64 < /path/to/test.png | tr -d '\n')
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -d "{
    \"model\": \"gpt-4o\",
    \"messages\": [{
      \"role\": \"user\",
      \"content\": [
        {\"type\": \"text\", \"text\": \"Describe this image.\"},
        {\"type\": \"image_url\", \"image_url\": {\"url\": \"data:image/png;base64,${IMAGE_B64}\"}}
      ]
    }]
  }"
```

**Expected**: HTTP 200, `choices[0].message.content` is a non-empty string.

---

### AC-3: Non-vision model rejected

```bash
curl -s -w "\n%{http_code}" -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -d '{
    "model": "command-r-plus",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "What is this?"},
        {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}}
      ]
    }]
  }'
```

**Expected**: HTTP 400, response body contains `"error": "vision_model_required"` and names `"command-r-plus"` in `detail.requested_model`. Request does NOT reach LiteLLM (verified by absence of LiteLLM access log entry for this request).

---

### AC-4: Streaming + image rejected

```bash
curl -s -w "\n%{http_code}" -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -d '{
    "model": "gpt-4o",
    "stream": true,
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "Describe."},
        {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}}
      ]
    }]
  }'
```

**Expected**: HTTP 400, `"error": "vision_streaming_not_supported"`.

---

### AC-5: No image data in audit log

```bash
make logs svc=guardrails | grep '"event_type":"inference_request"' | tail -1
```

After sending a vision request: **Expected**: log line contains `"image_part_count": 1` (or the count of images sent). Log line does NOT contain `base64`, `data:image`, or any URL from the request.

---

### AC-6: Text-only regression

```bash
curl -s -w "\n%{http_code}" -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'
```

**Expected**: HTTP 200. Response identical in shape to pre-feature behaviour.

---

### AC-7: Memory budget not exceeded

```bash
make stats
```

**Expected**: Total memory across all running containers ≤ profile limit. No container added; the `guardrails` container memory increase from the in-memory cache is negligible (< 1 MB).
