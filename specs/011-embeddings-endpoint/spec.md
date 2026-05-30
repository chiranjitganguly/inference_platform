# Feature Specification: Embeddings Endpoint

**Feature Branch**: `011-embeddings-endpoint`

**Created**: 2026-05-30

**Status**: Draft

**Input**: User description: "Build an embeddings endpoint that converts text into vector representations for RAG pipelines. Callers must specify which embedding model to use and receive a float array of the appropriate dimensionality alongside token usage. Embedding models and chat models must be clearly separated — using a chat model on the embeddings endpoint must be disallowed."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Convert Text to Embeddings (Priority: P1)

A RAG pipeline or downstream service sends one or more text strings to the embeddings endpoint, specifying an embedding model by name. The system returns a float array of the correct dimensionality for the model and reports how many tokens were consumed.

**Why this priority**: Core value of the feature — without this, nothing else is testable.

**Independent Test**: Send a POST request with a valid embedding model and a text input; verify the response contains a float array of the correct length and a token usage count.

**Acceptance Scenarios**:

1. **Given** a caller submits `text-embedding-3-small` and a non-empty text string, **When** the request is processed, **Then** the response contains a float array of exactly 1536 elements and a non-zero token usage count.
2. **Given** a caller submits `text-embedding-3-large` and a non-empty text string, **When** the request is processed, **Then** the response contains a float array of exactly 3072 elements and a non-zero token usage count.
3. **Given** a caller submits a batch of multiple text strings, **When** the request is processed, **Then** the response contains one float array per input string, each of the correct dimensionality.

---

### User Story 2 - Reject Chat Models on Embeddings Endpoint (Priority: P1)

A caller mistakenly (or maliciously) attempts to use a chat/completion model (e.g., `gpt-4o`, `claude-sonnet`) on the embeddings endpoint. The system rejects the request with a clear error message before any upstream call is made.

**Why this priority**: Equal to P1 — model type separation is a hard constraint that must be enforced at the gateway layer.

**Independent Test**: Send a POST request naming a chat model; verify the response is a 4xx error with a message that identifies the model type mismatch.

**Acceptance Scenarios**:

1. **Given** a caller submits `gpt-4o` as the model on the embeddings endpoint, **When** the request is received, **Then** the system returns a 400-class error indicating the model is not an embedding model.
2. **Given** a caller submits `claude-sonnet` as the model on the embeddings endpoint, **When** the request is received, **Then** the system returns a 400-class error and does not forward the request upstream.
3. **Given** a caller submits an unknown model name, **When** the request is received, **Then** the system returns a 400-class error stating the model is unrecognised.

---

### User Story 3 - Token Usage Reporting (Priority: P2)

A platform operator or API consumer needs to track token consumption for embedding calls for cost attribution, just as they do for chat completions.

**Why this priority**: Needed for spend tracking but does not block the core embedding functionality.

**Independent Test**: Submit a known text string; verify that the `usage.prompt_tokens` (or equivalent) field in the response is a positive integer consistent with the model's tokenisation.

**Acceptance Scenarios**:

1. **Given** a valid embedding request, **When** the response is returned, **Then** `usage.prompt_tokens` and `usage.total_tokens` are present and non-zero.
2. **Given** a batch embedding request with multiple inputs, **When** the response is returned, **Then** `usage.total_tokens` reflects the sum of tokens across all inputs.

---

### Edge Cases

- What happens when the input text is an empty string?
- What happens when the batch size exceeds a reasonable upper limit?
- What happens when the upstream embedding provider is unavailable?
- How does the system behave if the caller omits the `model` field entirely?
- What happens when input text exceeds the model's token limit (e.g., >8192 tokens for `text-embedding-3-small`)?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose an embeddings endpoint compatible with the OpenAI `/v1/embeddings` request and response schema.
- **FR-002**: Callers MUST specify an embedding model by name in every request; requests without a model field MUST be rejected with a 400 error.
- **FR-003**: The system MUST enforce that only models carrying `type: embedding` in their metadata are accepted on the embeddings endpoint; any other model type MUST result in a 400 error before any upstream call is made.
- **FR-004**: The system MUST return a float array of exactly 1536 elements for `text-embedding-3-small` and exactly 3072 elements for `text-embedding-3-large`.
- **FR-005**: Every response MUST include token usage fields (`prompt_tokens`, `total_tokens`) reflecting actual upstream consumption.
- **FR-006**: Embedding requests MUST NOT be stored in or served from the response cache; each request MUST reach the upstream provider.
- **FR-007**: The system MUST support batch embedding requests (multiple input strings in a single call) and return one vector per input in the same order.
- **FR-008**: Embedding calls MUST NOT be forwarded to Phoenix Arize or Langfuse; they are infrastructure calls and must not appear as LLM reasoning traces.
- **FR-009**: The gateway MUST apply authentication and rate limiting to the embeddings endpoint using the same mechanism as chat completion endpoints.
- **FR-010**: Input text that exceeds the model's maximum token limit MUST result in a clear error response rather than silent truncation.

### Key Entities

- **EmbeddingRequest**: Input object containing `model` (string, required), `input` (string or array of strings), and optional `encoding_format`.
- **EmbeddingObject**: Single vector result containing `object: "embedding"`, `index` (position in batch), and `embedding` (float array).
- **EmbeddingResponse**: Response envelope containing `object: "list"`, array of `EmbeddingObject`, `model` (echo of requested model), and `usage` block.
- **EmbeddingModel**: Catalogue entry with `type: embedding`, `model_name`, and `dimensions` (1536 or 3072). Distinct from chat model entries which carry `type: chat`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A valid single-text embedding request completes and the caller receives a correctly-sized float array in under 3 seconds under normal load.
- **SC-002**: 100% of requests naming a non-embedding model are rejected at the gateway without reaching the upstream provider.
- **SC-003**: Token usage is reported in every successful response with zero omissions across a suite of test inputs.
- **SC-004**: Batch requests containing up to 100 inputs return one vector per input in correct positional order with a single upstream call.
- **SC-005**: No embedding request appears in Phoenix Arize spans or Langfuse traces during a verification run of 50 consecutive embedding calls.
- **SC-006**: Embedding results are never served from cache — re-submitting an identical request twice produces two upstream calls, verifiable via upstream provider logs or spend counters.

## Assumptions

- The two supported embedding models are `text-embedding-3-small` (1536 dimensions) and `text-embedding-3-large` (3072 dimensions), both already present in the LiteLLM model catalogue.
- Model type separation is enforced via a `type: embedding` metadata field on model catalogue entries; chat models do not carry this field.
- The embeddings endpoint is reachable through Kong on the same proxy port (`:8080`) as chat completions, under the path `/v1/embeddings`.
- Authentication and rate limiting reuse existing Kong plugins already configured for chat completion routes — no new auth mechanism is required.
- Embedding calls count against the same virtual key spend budget as chat calls, enabling unified cost attribution.
- Prometheus metrics for embedding token usage are emitted via the existing `prometheus` callback in LiteLLM; no new metrics pipeline is needed.
- The `encoding_format` parameter defaults to `float`; `base64` encoding is out of scope for this feature.
- Mobile or browser-direct access to the embeddings endpoint is out of scope — all callers are server-side services.
