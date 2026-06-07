# Research: Structured JSON Output

**Branch**: `020-structured-json-output` | **Date**: 2026-06-07

---

## Decision 1 — Schema Self-Validation Library

**Decision**: Use `jsonschema` (Python package) with the draft-07 meta-schema to validate the caller-provided schema before forwarding the request.

**Rationale**:
- `jsonschema` is the de facto standard Python JSON Schema validator; draft-07 support is stable and well-tested.
- Meta-schema validation (`jsonschema.Draft7Validator.check_schema(schema)`) runs in < 1 ms for typical schemas — well within the 5 ms p95 gate target.
- No new Docker service required; `jsonschema` is a pure-Python pip dependency.
- The guardrails service's `requirements.txt` already pulls from the Python ecosystem; adding one package follows the established pattern.

**Alternatives considered**:
- `fastjsonschema` — faster but less complete draft-07 coverage; edge cases around `$ref` resolution differ from jsonschema. Rejected: correctness outweighs marginal speed gain at this scale.
- Custom regex/type-check approach — would miss many invalid schema constructs. Rejected: incomplete coverage creates false confidence.

---

## Decision 2 — Native vs. Prompt-Based Model Classification

**Decision**: Classify models by `provider` field from LiteLLM's `/model/info` response after filtering by `"json_schema" in capabilities`:

| Class | Models | LiteLLM behaviour |
|---|---|---|
| **Native** | `gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `o4-mini`, `claude-sonnet`, `claude-haiku` | LiteLLM passes `response_format` as-is to the provider API |
| **Prompt-based** | `gemini-pro`, `gemini-flash`, `command-r-plus` | LiteLLM injects a system prompt instructing the model to output valid JSON |

**Rationale**:
- LiteLLM Proxy natively routes `response_format.type: "json_schema"` to OpenAI and Anthropic provider APIs without modification. This guarantees provider-level JSON Schema enforcement for those models.
- For Google and Cohere, LiteLLM's built-in fallback adds a system prompt — no extra Guardrails logic is needed. The post-proxy validation loop still applies and is especially important for these models.
- The `StructuredOutputCapabilityCache` stores both sets; the capability gate checks `all_json_schema_models` (union). Native vs. prompt-based distinction is informational — logged via startup log line.

**Alternatives considered**:
- Blocking prompt-based models entirely — rejected: the spec explicitly supports them, and the retry loop compensates for their lower reliability.
- Per-model retry budget — rejected: adds complexity without clear benefit at this stage. A single configurable global max suffices.

---

## Decision 3 — Phoenix Span Attribute Injection

**Decision**: Inject `metadata.schema_name` and `metadata.schema_hash` into the LiteLLM request body before forwarding. LiteLLM's `arize_phoenix` callback propagates `metadata` fields as custom attributes on the Phoenix LLM span.

**Rationale**:
- LiteLLM's `arize_phoenix` callback reads the `metadata` dict from the request and adds each key as a span attribute on the `LLM` span it creates. This is the same mechanism used by the embeddings `no_log` injection in `main.py`.
- No custom OTel instrumentation is needed — the existing callback handles it.
- `schema_name` is a user-chosen identifier (e.g., `"invoice_schema"`) — it is metadata, not prompt content. Constitution §II permits it in spans.
- `schema_hash` (12-hex SHA-256 of sorted schema JSON) allows grouping by schema version without storing the schema itself.

**Alternatives considered**:
- OTel span injection via `opentelemetry.trace.get_current_span().set_attribute()` directly in Guardrails — rejected: the active span at the Guardrails layer is a `CHAIN` span, not the `LLM` span that LiteLLM creates downstream. Injecting there would put the attribute on the wrong span.
- Passing schema name via a custom request header — rejected: LiteLLM strips unknown headers; metadata dict is the documented mechanism.

---

## Decision 4 — Retry Loop Architecture

**Decision**: Implement the retry loop inside `main.py`'s `proxy()` function as a for-loop that re-issues the (unmodified) original request body to LiteLLM up to `STRUCTURED_OUTPUT_MAX_RETRIES` additional times on validation failure. No correction hint is added to retried requests.

**Rationale**:
- For native json_schema models (OpenAI, Anthropic), the provider itself enforces schema conformance — validation failure in practice means a transient provider issue, not a systematic schema problem. Retrying the same request is correct.
- For prompt-based models, the model may occasionally miss the JSON constraint. Retrying gives it another attempt; adding a correction hint risks inflating prompt tokens and making the retry strategy model-specific.
- The existing `httpx.AsyncClient(timeout=120.0)` in `proxy()` is reused inside the loop — no new client needed.
- `STRUCTURED_OUTPUT_MAX_RETRIES` env var (default `"3"`) allows operators to tune without code changes.

**Alternatives considered**:
- Correction-hint retry (append `"Your response must be valid JSON conforming to the schema"` to messages) — rejected: complicates retry logic, changes token usage, risks content policy issues on some providers.
- Exponential backoff between retries — rejected: validation failures are not rate-limit related; immediate retries are appropriate and keep latency predictable.
- Delegating retry to LiteLLM's built-in `num_retries` — rejected: LiteLLM retries on provider errors (5xx), not on schema validation failures (which are 200 responses with non-conforming content).

---

## Decision 5 — Streaming Guard

**Decision**: Reject `stream: true` requests that also include `response_format.type: "json_schema"` with a `400 structured_output_streaming_not_supported` error.

**Rationale**:
- Streaming delivers content incrementally; the full JSON string is only available after the stream completes. Validating a streaming response requires buffering the entire stream, which eliminates the latency benefit and adds buffer-overflow risk.
- The spec explicitly defers streaming + structured output to a future feature.
- Pattern mirrors the function-calling streaming guard (FR-009 in feature 019), keeping the gate logic consistent across the codebase.

---

## Decision 6 — `strict` Field Handling

**Decision**: `strict` defaults to `true` and no lenient mode exists. The platform passes `strict: true` in the `response_format` object to native providers. For prompt-based providers, LiteLLM's system prompt already enforces `additionalProperties: false` behaviour.

**Rationale**: Resolved in `/speckit-clarify` session 2026-06-07 (answer A). Eliminates an ambiguous code path and matches OpenAI's production recommendation.

---

## Decision 7 — Error Envelope

**Decision**: All validation rejections use the OpenAI error envelope:
```json
{
  "error": {
    "message": "Human-readable description",
    "type": "machine_readable_code",
    "code": "machine_readable_code"
  }
}
```

Schema conformance failure (post-proxy, 422) adds two extra top-level fields:
```json
{
  "error": { "type": "schema_conformance_failure", ... },
  "retry_count": 3,
  "schema_name": "invoice_schema"
}
```

**Rationale**: Consistent with ADR-018 (established in feature 019 for all `/v1/chat/completions` validation rejections). The extra fields on the 422 are additive and do not break OpenAI SDK compatibility.
