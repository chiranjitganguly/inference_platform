# Feature Specification: Model Catalogue API

**Feature Branch**: `004-model-catalogue-api`

**Created**: 2026-05-28

**Status**: Draft

**Input**: User description: "Build an endpoint that lists all available AI models and their capabilities so developers can programmatically select the right model. Each model entry must include its identifier, owning provider, and a metadata block describing its tier, context window, and supported capabilities. Requests without a valid API key must be rejected. The catalogue must include models from at least four distinct providers."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Browse Full Model Catalogue (Priority: P1)

A developer wants to discover which AI models are available on the platform so they can choose the right one for their use case.

**Why this priority**: This is the core capability of the feature — without it, nothing else has value.

**Independent Test**: Send an authenticated GET request to the catalogue endpoint and verify that the response lists all 11 models across OpenAI, Anthropic, Google, and Cohere, each with identifier, provider, tier, context window, and capabilities.

**Acceptance Scenarios**:

1. **Given** a developer with a valid API key, **When** they call `GET /v1/models`, **Then** the response is HTTP 200 with a JSON array containing all 11 models, each with `id`, `provider`, `tier`, `context_window`, `capabilities`, and `type` fields.
2. **Given** a developer with a valid API key, **When** they call `GET /v1/models`, **Then** the response includes models from exactly four providers: `openai`, `anthropic`, `google`, and `cohere`.
3. **Given** a developer with a valid API key, **When** they call `GET /v1/models`, **Then** every model entry has `tier` set to either `standard` or `premium` — no other value is permitted.

---

### User Story 2 - Distinguish Chat vs Embedding Models (Priority: P1)

A developer building a retrieval-augmented generation pipeline needs to find only embedding models, while a developer building a chat application needs only chat models.

**Why this priority**: Without the `type` distinction, developers cannot filter programmatically, making the catalogue significantly less useful.

**Independent Test**: Call the catalogue endpoint and verify that chat models carry `type: chat` and embedding models carry `type: embedding` plus a `dimensions` field with a positive integer value.

**Acceptance Scenarios**:

1. **Given** a valid API key, **When** the developer calls `GET /v1/models`, **Then** `text-embedding-3-small` and `text-embedding-3-large` have `type: embedding` and a `dimensions` integer field; all other models have `type: chat` with no `dimensions` field.
2. **Given** a valid API key, **When** the developer filters the response by `type: embedding`, **Then** exactly 2 models are returned.
3. **Given** a valid API key, **When** the developer filters the response by `type: chat`, **Then** exactly 9 models are returned.

---

### User Story 3 - Reject Unauthenticated Requests (Priority: P1)

A developer or automated client without a valid API key must not be able to access the catalogue.

**Why this priority**: Security gate — public exposure of the catalogue without auth violates the platform's Kong → Guardrails → LiteLLM chain and key management policy.

**Independent Test**: Call `GET /v1/models` with no API key, an expired key, and a malformed key; verify that all three cases return HTTP 401 with a machine-readable error body.

**Acceptance Scenarios**:

1. **Given** no API key is provided, **When** the client calls `GET /v1/models`, **Then** the response is HTTP 401 with `{"error": {"code": "unauthorized", "message": "..."}}`.
2. **Given** an invalid or revoked API key is provided, **When** the client calls `GET /v1/models`, **Then** the response is HTTP 401.
3. **Given** a malformed Authorization header (wrong scheme), **When** the client calls `GET /v1/models`, **Then** the response is HTTP 401.

---

### Edge Cases

- A model that is configured but temporarily disabled MUST appear in the catalogue with an added `status: unavailable` field; enabled models carry `status: available`. This allows callers to detect the model exists without being able to use it.
- What does the system return for a valid key that belongs to a virtual key with no model-access grants?
- How does the system behave if LiteLLM's own config is malformed or unreachable at request time?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The platform MUST expose a `GET /v1/models` endpoint that returns the full model catalogue.
- **FR-002**: Every model entry MUST include: `id` (string), `provider` (string), `tier` (`standard` | `premium`), `type` (`chat` | `embedding`), `status` (`available` | `unavailable`), `context_window` (integer, tokens), `capabilities` (array of strings).
- **FR-003**: Embedding models MUST additionally include a `dimensions` field (positive integer); chat models MUST NOT include `dimensions`.
- **FR-004**: The catalogue MUST include all 11 configured models spanning four providers: OpenAI (`gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `o4-mini`), Anthropic (`claude-sonnet`, `claude-haiku`), Google (`gemini-flash`, `gemini-pro`), Cohere (`command-r-plus`), and OpenAI embeddings (`text-embedding-3-small`, `text-embedding-3-large`).
- **FR-005**: Requests without a valid platform API key MUST be rejected with HTTP 401 before the catalogue data is returned.
- **FR-006**: The response MUST be served as `application/json`.
- **FR-007**: The catalogue data MUST be sourced from or consistent with the active LiteLLM model configuration — no hardcoded shadow catalogue.
- **FR-008**: The endpoint MUST be reachable only through the Kong gateway (port 8080); no direct access to the underlying service port.

### Key Entities

- **ModelEntry**: Represents one available model. Attributes: `id`, `provider`, `tier`, `type`, `status`, `context_window`, `capabilities[]`, optional `dimensions`.
- **Catalogue**: The ordered collection of all active ModelEntry objects returned by the endpoint.
- **ApiKey**: A platform-issued key validated by Kong before the request reaches the catalogue service.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A developer with a valid API key receives the full catalogue in under 200 ms (p95) under normal load.
- **SC-002**: The catalogue always contains at least 11 entries from exactly 4 providers — verified by an automated smoke test on every deployment.
- **SC-003**: 100% of unauthenticated requests receive HTTP 401; 0% reach the catalogue data layer.
- **SC-004**: Every model entry passes schema validation (required fields present, correct types, tier in `{standard, premium}`, type in `{chat, embedding}`, status in `{available, unavailable}`, dimensions present iff type is embedding) — verified by contract test.
- **SC-005**: The catalogue endpoint is covered by the existing `make smoke` script and returns a non-empty model list within the script's timeout.

## Assumptions

- The portal-backend service (port 8092) is the natural host for this catalogue endpoint, as it already serves the cost estimator and catalogue API.
- LiteLLM's model configuration in `services/litellm/config.yaml` is the single source of truth; the catalogue endpoint reads from it (or a derived cache) rather than maintaining a separate list.
- Tier classification (`standard` / `premium`) is determined by the operator at configuration time and stored alongside the model entry in the LiteLLM config or a sidecar mapping.
- Capabilities strings are a controlled vocabulary (e.g., `chat`, `function-calling`, `vision`, `embeddings`, `streaming`) defined at configuration time.
- The endpoint is stateless — no per-user filtering of models based on virtual key grants (all keys see the same catalogue); access control is binary (valid key → full catalogue, no key → 401).
- Context window sizes are sourced from provider-published values and embedded in the LiteLLM config or a companion mapping file.

## Clarifications

### Session 2026-05-28

- Q: Which providers and models must the catalogue include? → A: OpenAI, Anthropic, Google, and Cohere — all 11 models currently configured in the platform.
- Q: What tier values are permitted? → A: Exactly two: `standard` and `premium`; every model entry must carry one.
- Q: How must embedding models be identified and what extra data must they carry? → A: `type: embedding` plus a `dimensions` integer; chat models carry `type: chat` with no `dimensions` field.
- Q: How must the response distinguish chat from embedding models? → A: Via the `type` field (`chat` | `embedding`) present on every model entry; embedding models additionally expose `dimensions`.
- Q: Should disabled/unavailable models appear in the catalogue? → A: Yes — include them with `status: unavailable`; enabled models carry `status: available`.
