# Feature Specification: Multimodal Image Support

**Feature Branch**: `018-multimodal-image-support`

**Created**: 2026-06-06

**Status**: Clarified

**Input**: User description: "Build multimodal support on the chat completion endpoint so callers can include images alongside text in a single request. Both URL-referenced and base64-encoded images must be accepted. Only models that declare vision support may be used. The text description of the image must appear in the response."

## Clarifications

### Session 2026-06-06

- Q: How is vision capability declared on a model? → A: `supports_vision: true` in model metadata; only models carrying this flag may accept image content.
- Q: What formats must image inputs support? → A: Both `https://` URL references and `data:image/...;base64,...` data URIs must be accepted.
- Q: What values does the `detail` field accept? → A: `low`, `high`, `auto` — controls image fidelity passed to the upstream model.
- Q: What must happen when a non-vision model is used with image content? → A: HTTP 400 with a structured, human-readable error naming the model and stating it does not support image inputs.
- Q: How are vision requests traced in Phoenix? → A: Phoenix Arize spans include image token counts as a span attribute where the upstream model returns them; omit the attribute when not available.
- Q: What is the default value for the `detail` field when a caller omits it? → A: `"auto"` — the gateway injects `detail: "auto"` before forwarding to the upstream model.
- Q: What error schema must vision-validation rejections use? → A: OpenAI error envelope — `{"error": {"message": "...", "type": "...", "code": "..."}}`. **Requires ADR amending constitution §4.4 before implementation.**
- Q: Is there a per-image size limit enforced at the gateway? → A: 5 MB per image; requests with any image exceeding this are rejected with HTTP 413 before reaching the upstream provider.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Send Image via URL with Text Prompt (Priority: P1)

A caller submits a chat completion request that includes a publicly accessible image URL alongside a text question. The system routes the request to a vision-capable model and returns a textual response that describes or reasons about the image.

**Why this priority**: This is the most common multimodal use case and validates the core routing and response pipeline. URL-based images require no client-side encoding and represent the simplest integration path.

**Independent Test**: Can be fully tested by sending a single chat completion request with a URL image part and verifying the response contains a meaningful text description of that image.

**Acceptance Scenarios**:

1. **Given** a caller sends a chat completion request with a `content` array containing one `image_url` part and one `text` part, **When** the request is submitted to the endpoint, **Then** the response is HTTP 200 and the `choices[0].message.content` contains a textual description referencing the image.
2. **Given** a caller provides an inaccessible or invalid image URL, **When** the request is submitted, **Then** the response is an HTTP error (4xx or 5xx) with a descriptive error message indicating the image could not be retrieved.

---

### User Story 2 - Send Base64-Encoded Image with Text Prompt (Priority: P1)

A caller encodes an image as a base64 string and submits it alongside a text question in a single chat completion request. The system accepts the encoded image, passes it to a vision-capable model, and returns a text response describing or reasoning about the image.

**Why this priority**: Base64 encoding allows callers to send images that are not publicly hosted (e.g., local files, private assets). Parity with URL support is essential for a complete multimodal integration.

**Independent Test**: Can be fully tested by base64-encoding any image locally, including it in a request alongside a text prompt, and verifying the response references the image content.

**Acceptance Scenarios**:

1. **Given** a caller sends a chat completion request with a `content` array containing one base64-encoded image part (`data:image/...;base64,...`) and one `text` part, **When** the request is submitted, **Then** the response is HTTP 200 and the `choices[0].message.content` contains a textual description referencing the image.
2. **Given** a caller sends a malformed or truncated base64 string, **When** the request is submitted, **Then** the response is HTTP 400 with a message indicating the image data is invalid.

---

### User Story 3 - Vision-Incapable Model Rejected (Priority: P2)

A caller explicitly requests a model that does not support vision (e.g., a text-only or embedding model) while including an image in the request. The system must detect this mismatch early and reject the request with a clear error rather than forwarding it and receiving a confusing downstream failure.

**Why this priority**: Without this guard, callers would receive cryptic upstream errors. Explicit rejection improves developer experience and prevents unnecessary LLM API calls.

**Independent Test**: Can be fully tested by sending a multimodal request that explicitly names a non-vision model and confirming the rejection response with an appropriate error code.

**Acceptance Scenarios**:

1. **Given** a caller includes an image part in a chat completion request and specifies a model not in the vision-capable model list, **When** the request is submitted, **Then** the response is HTTP 400 with an error body indicating the requested model does not support image inputs.
2. **Given** no model is specified and all eligible fallback candidates are non-vision models, **When** the request is submitted, **Then** the response is HTTP 400 with a message indicating no vision-capable model is available.

---

### User Story 4 - Multiple Images in a Single Request (Priority: P3)

A caller sends a chat completion request containing more than one image (via URL or base64, or a mix) alongside a text prompt. The model processes all images together and returns a unified textual response.

**Why this priority**: Multi-image requests are a natural extension once single-image support is established. Useful for comparison tasks (e.g., "what is different between these two images?").

**Independent Test**: Can be fully tested by sending two images in one request and verifying the response meaningfully addresses both.

**Acceptance Scenarios**:

1. **Given** a caller sends a request with two image parts and one text part, **When** the request is submitted, **Then** the response is HTTP 200 and the `choices[0].message.content` references content from both images.
2. **Given** a caller sends more images than the configured per-request maximum, **When** the request is submitted, **Then** the response is HTTP 400 with a message indicating the image count limit was exceeded.

---

### Edge Cases

- What happens when an image URL redirects to a non-image resource (HTML page, PDF)?
- How does the system handle an image MIME type that a specific vision model does not support (e.g., TIFF, SVG)?
- What happens if the base64 payload decodes to a file that is not an image?
- How does the system behave when an image is within the accepted format but exceeds the size limit?
- What happens when a request mixes URL and base64 images in the same `content` array?
- How are vision-model fallback chains handled when the primary vision model is unavailable?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The chat completion endpoint MUST accept a `content` array containing a mix of `text` and `image_url` parts in the same request, following the OpenAI multimodal message format.
- **FR-002**: The system MUST support `image_url` parts where the URL references a publicly accessible image (HTTP/HTTPS).
- **FR-003**: The system MUST support `image_url` parts where the `url` field contains a base64-encoded data URI in the format `data:<mime-type>;base64,<data>`.
- **FR-004**: The system MUST maintain a registry of models that declare vision capability via `supports_vision: true` in their metadata; only those models may be selected for requests containing image parts.
- **FR-005**: The system MUST reject requests containing image parts that specify (or would fall back exclusively to) models without `supports_vision: true`, returning HTTP 400 using the OpenAI error envelope (`{"error": {"message": "...", "type": "invalid_request_error", "code": "vision_model_required"}}`) that names the unsupported model. This schema applies to all vision-validation rejections. (Requires ADR amending constitution §4.4.)
- **FR-006**: The response to a successful multimodal request MUST include a textual description or analysis of the image in `choices[0].message.content`.
- **FR-007**: The system MUST enforce a configurable maximum number of images per request; requests exceeding this limit MUST be rejected with a 400-level error.
- **FR-008**: The system MUST validate image inputs before forwarding: base64 data URIs must be well-formed and the decoded payload must not exceed 5 MB per image; URL images must use `https://` and be a well-formed URL. The system MUST reject requests where any single image exceeds 5 MB with HTTP 413 and an OpenAI-envelope error body (`code: "image_too_large"`).
- **FR-009**: Existing fallback chain logic MUST be applied within the set of vision-capable models only; non-vision models MUST NOT appear in the vision fallback path.
- **FR-010**: The guardrails pipeline MUST be applied to multimodal requests in the same way as text-only requests; image content MUST pass through any applicable content scanning steps.
- **FR-011**: All multimodal requests MUST be traced end-to-end with the same observability signals (correlation ID, OTel spans, LLM spans) as text-only requests. Phoenix Arize spans for vision requests MUST include image token counts as a span attribute where the upstream model returns them; when not returned, the attribute MUST be omitted (not defaulted to zero).
- **FR-012**: Prompt content constraints MUST continue to be upheld: image data (base64 payloads or URL contents) MUST NOT be persisted in any log, trace, or audit store.

### Key Entities

- **Multimodal Message**: A chat message whose `content` field is an array of typed parts (`text` and `image_url`). Key attributes: parts array, associated model, request ID.
- **Image Part**: A single image within a message. Key attributes: type (`image_url`), source (`url` string — either an `https://` URL or a `data:image/...;base64,...` data URI), `detail` fidelity hint (allowed values: `low`, `high`, `auto`; default: `"auto"` injected by the gateway when omitted).
- **Vision-Capable Model**: A model entry in the model catalogue that declares support for image inputs via `supports_vision: true` in its metadata. Key attributes: model name, `supports_vision` flag, supported image MIME types, maximum image count per request, maximum image size.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Callers can successfully receive a text description of an image by including it in a chat completion request, with end-to-end round-trip time no greater than 2× the baseline for an equivalent text-only request to the same model.
- **SC-002**: 100% of requests specifying or falling back to a non-vision model while including image parts are rejected before reaching the upstream LLM provider.
- **SC-003**: Both URL-referenced and base64-encoded images are accepted with no functional difference in response quality or format.
- **SC-004**: Requests with invalid image data (malformed base64, unreachable URL, unsupported format) receive a structured error response within the same latency envelope as other 400-level validation rejections.
- **SC-005**: No image data (raw bytes, base64 strings, or URL contents) appears in any audit log, trace export, or metrics label.
- **SC-006**: The smoke-test suite passes with no regressions on text-only chat completion requests after the feature is deployed.
- **SC-007**: Phoenix Arize spans for vision requests include an image token count attribute for all models that return token usage; operators can filter and aggregate vision token consumption in Grafana using the `phoenix_` metrics prefix.

## Assumptions

- The OpenAI multimodal message format (`content` as an array of typed parts) is the accepted wire format; no proprietary image attachment mechanism will be added.
- The initial set of vision-capable models is: `gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `claude-sonnet`, `gemini-flash`, `gemini-pro` — derived from the existing model catalogue; the Cohere and embedding models are non-vision.
- A maximum of 5 images per request is a reasonable default; this is configurable without a code change.
- Supported image MIME types default to `image/jpeg`, `image/png`, `image/gif`, `image/webp` — the intersection of what the listed vision models accept.
- URL-referenced images are fetched by the upstream LLM provider, not by the gateway itself; the gateway validates that the URL is a well-formed HTTP/HTTPS URL but does not proxy the image download.
- Base64 data URIs are validated for well-formedness at the gateway before forwarding; content-level image analysis is delegated to the model.
- The guardrails service forwards image parts to the upstream pipeline unchanged; deep image content scanning (e.g., NSFW detection) is out of scope for this feature.
- Streaming responses (`stream: true`) are out of scope for the initial release; multimodal requests must use non-streaming mode.
- The existing LiteLLM model routing layer already supports the OpenAI multimodal format for vision models; configuration changes rather than custom proxy logic are expected to be sufficient.
