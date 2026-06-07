# Feature Specification: Structured JSON Output

**Feature Branch**: `020-structured-json-output`

**Created**: 2026-06-07

**Status**: Draft

---

## Clarifications

### Session 2026-06-07

- Q: How is structured output mode activated in a request? → A: Via a `response_format` object with `type: "json_schema"`, a `name` string (schema identifier), `strict: true`, and a `schema` object (the JSON Schema).
- Q: What type is the response `content` field? → A: Always a JSON string — a string value that, when parsed, yields an object conforming to the provided schema.
- Q: What does `strict: true` enforce? → A: Enforces `additionalProperties: false` on the schema, ensuring no properties beyond those declared in the schema appear in the response.
- Q: How are structured output requests traced in Phoenix? → A: Phoenix spans for these requests carry the schema `name` as a span attribute.
- Q: What is the platform's behaviour when `strict` is omitted or set to `false`? → A: `strict` defaults to `true`; omitting it is treated identically to `strict: true`. No lenient mode exists — there is one structured output mode and it always enforces `additionalProperties: false`.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Schema-Constrained Inference Request (Priority: P1)

A developer calls the inference gateway with a JSON Schema alongside their prompt. The platform guarantees that the `content` field of every response is valid JSON that parses cleanly and conforms to the provided schema — with no extra steps required from the caller.

**Why this priority**: This is the core contract of the feature. Everything else is secondary to the guarantee that responses are always schema-valid.

**Independent Test**: Send 10 consecutive chat-completion requests each carrying a simple JSON Schema (e.g., `{type: object, properties: {answer: {type: string}, confidence: {type: number}}, required: [answer, confidence]}`). All 10 responses must parse as JSON and validate against the schema.

**Acceptance Scenarios**:

1. **Given** a valid inference request with a well-formed JSON Schema, **When** the request reaches the model, **Then** the response `content` field is valid JSON that passes schema validation.
2. **Given** 10 consecutive requests with the same schema, **When** all are processed, **Then** all 10 responses pass schema validation with no failures.
3. **Given** a schema requiring specific field types, **When** the model returns a response, **Then** all field types match the schema exactly (e.g., numbers are not returned as strings).

---

### User Story 2 — Invalid Schema Rejected Before Inference (Priority: P2)

A developer accidentally submits a malformed or semantically invalid JSON Schema. The platform rejects the request immediately with a clear, actionable error — no inference call is made, and no credits are consumed.

**Why this priority**: Early rejection saves cost and gives developers fast feedback without ambiguous model errors.

**Independent Test**: Submit a request with a broken schema (e.g., unknown `type` value, circular `$ref`, invalid keyword). Verify a 4xx error is returned with a message identifying the schema problem before any model call occurs.

**Acceptance Scenarios**:

1. **Given** a request with a syntactically invalid JSON Schema, **When** submitted, **Then** the platform returns a `400 Bad Request` with a human-readable description of the schema error.
2. **Given** a request with an unsupported schema construct (e.g., deeply recursive `$ref` beyond allowed depth), **When** submitted, **Then** the platform returns a `422 Unprocessable Entity` explaining the unsupported construct.
3. **Given** a valid schema, **When** submitted, **Then** no rejection occurs and inference proceeds normally.

---

### User Story 3 — Schema Conformance Failure Surfaced as a Distinct Error (Priority: P3)

In rare cases where the model cannot produce a schema-conforming response after all retry attempts, the platform returns a structured error distinguishing a schema-conformance failure from a general model or network error. The caller can decide how to handle it — retry, fall back, or surface to their user.

**Why this priority**: Callers need reliable error taxonomy to build robust client logic. A generic 500 that could mean "network hiccup" or "schema impossible to satisfy" is not actionable.

**Independent Test**: Send a deliberately contradictory schema (e.g., `minimum: 10, maximum: 5`) or one the model provably cannot satisfy. Verify the error code and message are distinct from generic model errors.

**Acceptance Scenarios**:

1. **Given** a schema the model cannot satisfy after all retries, **When** the final retry fails, **Then** the platform returns a `422` with `error.type: schema_conformance_failure` and a `retry_count` field.
2. **Given** a transient model error unrelated to the schema, **When** it occurs, **Then** the error code is distinct from `schema_conformance_failure`.
3. **Given** a schema conformance failure, **When** the error is returned, **Then** it includes the schema that was provided, so the caller can inspect what was attempted.

---

### Edge Cases

- What happens when the schema is valid but the requested model does not natively support structured output?
- What happens when the model returns valid JSON that does not match the schema on the first attempt — how many retries occur before a failure is declared?
- How does the system behave when the schema is extremely large (e.g., hundreds of properties)?
- What happens when the JSON Schema contains a `$ref` to an external URI?
- How are `anyOf` / `oneOf` / `allOf` combinators handled when validation is ambiguous? When `strict: true`, does `additionalProperties: false` apply recursively to nested object schemas within these combinators?
- What happens when the caller omits the schema — does standard inference continue unaffected?

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Callers MUST activate structured output mode by including a `response_format` object in their request containing: `type: "json_schema"`, a `name` string identifying the schema, `strict: true`, and a `schema` object (the JSON Schema defining the required response shape).
- **FR-002**: The platform MUST validate the provided schema itself before forwarding the request to any model — invalid schemas MUST be rejected with a descriptive error without triggering inference.
- **FR-003**: The platform MUST ensure the model's response `content` field is a JSON string — a string value that parses to a JSON object fully conforming to the caller-provided schema. The field is never a raw object; it is always a string.
- **FR-004**: When a model response fails schema validation, the platform MUST retry the request up to a configurable maximum (default: 3 retries) before returning a failure.
- **FR-005**: When all retries are exhausted and the response still does not conform, the platform MUST return a `422` response with `error.type: schema_conformance_failure` and include the retry count and the schema that was used.
- **FR-006**: The platform MUST pass the `response_format` parameters natively to models whose APIs support structured output. When `strict: true` is set, the platform MUST enforce `additionalProperties: false` on the schema, meaning properties not declared in the schema are disallowed in the response.
- **FR-007**: For models without native structured output support, the platform MUST enforce schema conformance via prompt-level instructions and post-response JSON validation.
- **FR-008**: Structured output mode MUST be opt-in — requests without a `response_format.type: "json_schema"` field MUST follow the existing inference path unmodified. The `strict` field defaults to `true`; there is no lenient mode. Omitting `strict` or setting it to `false` is treated identically to `strict: true`.
- **FR-009**: Schema validation MUST be performed by the platform, not delegated entirely to the model — the platform validates the model's raw output before returning it to the caller.
- **FR-010**: The platform MUST emit an observable signal (log field and metric counter) for each schema validation pass, retry, and failure, attributing it to the model and schema hash. Phoenix traces for structured output requests MUST include the schema `name` as a dedicated span attribute so requests can be filtered and grouped by schema in the trace UI.

### Key Entities

- **Structured Output Request**: An inference request that includes a `response_format` object with `type: "json_schema"`, a `name` (schema identifier used as a Phoenix span attribute), `strict: true`, and a `schema` payload. Also carries a model target and an optional retry limit override.
- **JSON Schema**: A caller-provided schema object conforming to JSON Schema draft-07 or later. Defines the required shape, types, and constraints for the response content.
- **Validation Result**: The outcome of checking a model response against the schema. States: `pass`, `retry`, `fail`. Carries a reason string on non-pass outcomes.
- **Schema Hash**: A deterministic identifier for a given schema, used for metric attribution and caching schema compilation.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 10 consecutive structured output requests with a valid schema all return schema-conforming JSON in the `content` field — a 100% pass rate across the 10-call sequence is the acceptance bar.
- **SC-002**: Invalid schemas are rejected in under 100 ms without triggering any model call, as measurable by absence of upstream model latency in the request trace.
- **SC-003**: Callers can reliably distinguish schema-conformance failures (`error.type: schema_conformance_failure`) from all other error types — zero ambiguous error codes for this failure mode.
- **SC-004**: Enabling structured output mode adds no more than one additional round-trip of latency versus a standard inference call in the non-retry case.
- **SC-005**: Schema validation pass, retry, and failure events are visible as distinct metric counters within the existing observability stack within 30 seconds of occurrence.

---

## Assumptions

- This feature builds on the function-calling capability introduced in feature 019; schema parameter routing through the gateway follows the same path.
- JSON Schema draft-07 is the minimum supported version; draft 2020-12 is a stretch goal and not required for initial delivery.
- External `$ref` URIs in schemas are not resolved — schemas must be self-contained. This is a deliberate security boundary.
- The maximum retry count is configurable per deployment but defaults to 3; callers may not override it above the platform maximum.
- Models that do not support native structured output (e.g., some Cohere or Google variants) are supported via prompt-based enforcement; their reliability under this mode may be lower and is documented per-model.
- The schema size limit follows the existing request body size limit already enforced by the Kong gateway (feature 015).
- Prompt content is never persisted, consistent with the platform constitution — the schema itself (as request metadata) follows the same rule.
- Streaming responses (`stream: true`) are out of scope for structured output mode in this iteration; a streaming + structured output combination is a future feature.
