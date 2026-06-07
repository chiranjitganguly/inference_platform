# Implementation Plan: Structured JSON Output

**Branch**: `020-structured-json-output` | **Date**: 2026-06-07 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/020-structured-json-output/spec.md`

---

## Summary

Extend `/v1/chat/completions` to accept a `response_format` object with `type: "json_schema"`, `name`, `strict: true`, and `schema`. The Guardrails service adds a **pre-proxy gate** (schema self-validity check, model capability gate, streaming guard) and a **post-proxy validation loop** (parse and validate `choices[0].message.content` against the caller schema, retry up to `STRUCTURED_OUTPUT_MAX_RETRIES` times on failure, return `422 schema_conformance_failure` if exhausted). LiteLLM natively passes `response_format` to OpenAI and Anthropic providers. For Google and Cohere, LiteLLM adds system-prompt enforcement. The schema `name` is injected as `metadata.schema_name` into the LiteLLM request body so the `arize_phoenix` callback surfaces it as a Phoenix span attribute. `config.yaml` gains a `json_schema` capability tag on the six natively supporting models.

---

## Technical Context

**Language/Version**: Python 3.12 (Guardrails service)

**Primary Dependencies**: FastAPI, httpx (async), `jsonschema` (draft-07 meta-schema validation for pre-proxy schema self-check) — all already present or standard library adjacent; `jsonschema` is the only new pip dependency

**Storage**: None — in-memory `StructuredOutputCapabilityCache` (loaded from LiteLLM `/model/info` at startup); schema compilation is per-request, no cache needed at this scale

**Testing**: pytest (contract tests `tests/contract/test_structured_output.py`), smoke test extension (`scripts/smoke-test.sh`)

**Target Platform**: Linux container (arm64 + amd64), Docker Compose `core` profile — guardrails is already in core; no new services

**Performance Goals**: Pre-proxy gate (schema self-check + model gate) ≤ 5 ms p95 per request; post-proxy validation (JSON parse + jsonschema validate) ≤ 3 ms p95 per attempt

**Constraints**:
- Schema content (the JSON Schema object) MUST NOT appear in any log, trace, or audit entry — only `schema_name` (identifier string) and `schema_hash` (12-hex digest) are permitted as metadata
- Response content (`choices[0].message.content`) MUST NOT be logged — only validation outcome (pass/fail) and retry count
- `stream: true` is rejected for structured output requests (same gate as function calling)
- `strict` defaults to `true`; no lenient mode

**Scale/Scope**: Shares existing per-consumer rate limits; retry loop multiplies LiteLLM calls by up to `MAX_RETRIES + 1` in the worst case (this is acceptable and expected)

---

## Constitution Check

| Principle | Status | Notes |
|---|---|---|
| **I — Request Flow Integrity** | PASS | Kong → Guardrails → LiteLLM chain unchanged. Pre-proxy gate and post-proxy retry loop are internal to Guardrails. No shortcutting. |
| **II — Prompt Content Ephemeral** | PASS | Schema content (the JSON Schema object) and response content (`choices[0].message.content`) are never logged or traced. Only `schema_name` (user-chosen identifier) and `schema_hash` (12-hex digest) appear in audit/span metadata. |
| **III — OpenAI Compatibility** | PASS | `response_format.type: "json_schema"` is the standard OpenAI structured output wire format. Existing clients using OpenAI SDK structured output work unchanged. All validation rejections use the OpenAI error envelope (ADR-018). |
| **IV — Defence in Depth** | PASS | Kong enforces request size (feature 015). Guardrails validates schema self-validity and model capability (content layer). LiteLLM routes to provider. Each layer independent. |
| **V — Falsifiable Acceptance Criteria** | PASS | All ACs are `curl` commands with explicit expected HTTP status + `choices[0].message.content` JSON parse + jsonschema validation pass; or explicit `error.type: schema_conformance_failure` assertions. |

---

## Project Structure

### Documentation (this feature)

```text
specs/020-structured-json-output/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── chat-completions-structured-output.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code Changes

```text
services/
└── guardrails/
    ├── structured_output.py    # NEW: capability cache, schema validation, response validation
    └── main.py                 # MODIFY: startup cache load, pre-proxy SO gate, post-proxy retry loop

services/litellm/
└── config.yaml                 # MODIFY: add json_schema capability to 6 native models

scripts/
└── smoke-test.sh               # MODIFY: add [020] structured output probe

tests/
└── contract/
    └── test_structured_output.py   # NEW: contract tests for all ACs
```

**No changes to**: `docker-compose.yml`, `scripts/seed-kong.sh`, any other service.

**Structure Decision**: Single-service modification. `structured_output.py` is a sibling to `vision.py` and `function_calling.py` — same module pattern, independent lifecycle. The retry loop lives in `main.py`'s proxy function alongside the existing post-proxy FC response validation.

---

## Phase 0: Research

Complete. See [research.md](research.md).

Key resolved decisions:
1. Schema self-validation uses `jsonschema` draft-07 meta-schema — only new pip dependency; no new Docker service
2. `json_schema` capability declared natively for 6 models: `gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `o4-mini` (OpenAI), `claude-sonnet`, `claude-haiku` (Anthropic)
3. Google and Cohere receive system-prompt enforcement via LiteLLM's built-in handling — no extra Guardrails logic needed; they are included in `prompt_model_names` set and pass the capability gate
4. Phoenix span attribute: inject `metadata.schema_name` into LiteLLM request body — `arize_phoenix` callback picks it up as a custom metadata span attribute
5. Retry loop: Guardrails issues up to `STRUCTURED_OUTPUT_MAX_RETRIES` (default `3`, env-configurable) re-requests to LiteLLM using the original body; no correction hint is added (the native json_schema mode at the provider level already constrains generation)
6. Schema hash: `hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:12]` — used for metric attribution and audit log, never for content caching
7. All validation rejections use OpenAI error envelope (ADR-018), same as vision and function calling

---

## Phase 1: Design & Contracts

Complete. See linked artifacts below.

---

### Design Decisions

#### `services/guardrails/structured_output.py` (new)

Mirrors `function_calling.py` in structure. All public functions are pure and stateless except the cache.

**`StructuredOutputCapabilityCache`** (dataclass):
- `native_model_names: frozenset[str]` — models where LiteLLM passes `response_format` natively (OpenAI + Anthropic)
- `prompt_model_names: frozenset[str]` — models where LiteLLM uses system-prompt enforcement (Google, Cohere)
- `all_json_schema_models: frozenset[str]` — union; used by the capability gate
- `load()` — async; reads `/model/info`, inspects `"json_schema" in capabilities`; fail-fast if LiteLLM unreachable

**`has_structured_output_request(body: dict) -> bool`**:
Returns `True` iff `body.get("response_format", {}).get("type") == "json_schema"`.

**`validate_structured_output_request(body: dict, cache: StructuredOutputCapabilityCache) -> tuple[dict, int] | None`**:
Validation order (returns on first failure, OpenAI envelope):
1. `stream: true` → 400 `structured_output_streaming_not_supported`
2. `response_format.name` absent or not a non-empty string → 400 `invalid_structured_output_request` ("name is required")
3. `response_format.schema` absent or not a dict → 400 `invalid_structured_output_request` ("schema is required")
4. Model not in `cache.all_json_schema_models` → 400 `structured_output_model_required` (names the model, lists valid alternatives)
5. Schema fails draft-07 meta-schema validation → 422 `invalid_json_schema` (includes `jsonschema` error message)

**`inject_schema_metadata(body: dict, schema_name: str, schema_hash: str) -> dict`**:
Injects `metadata.schema_name` and `metadata.schema_hash` into the request body. The `arize_phoenix` callback surfaces `metadata` fields as custom span attributes in Phoenix.

**`compute_schema_hash(schema: dict) -> str`**:
`hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:12]`

**`validate_structured_output_response(response_body: dict, schema: dict) -> tuple[dict, int] | None`**:
- Extracts `choices[0].message.content`
- Parses as JSON — failure → `(envelope, 502)` `upstream_invalid_json`
- Validates parsed object against `schema` using `jsonschema.validate()` — failure → `(envelope, None)` (signals retry needed, not a final 502)
- Returns `None` on pass
- Returns `({"error": {"type": "schema_conformance_failure", ...}, "retry_count": N, "schema_name": name}, 422)` after exhausted retries (called from `main.py`, not from this function directly)

#### `services/guardrails/main.py` changes

**Lifespan** (`_lifespan`):
- Load `StructuredOutputCapabilityCache` alongside vision and FC caches; store as `app.state.so_cache`
- Log: `StructuredOutputCapabilityCache loaded: %d native, %d prompt-based models`

**Pre-proxy** (inside `proxy()` for `v1/chat/completions` POST):
```
_schema_name = None
_schema = None
_schema_hash = None

if has_structured_output_request(body):
    result = await _validate_structured_output(body, request)
    if isinstance(result, Response): return result  # gate rejected
    body, _schema_name, _schema, _schema_hash = result
```

**Post-proxy retry loop** (replaces single upstream call for SO requests):
```
if _schema_name is not None:
    for attempt in range(1, MAX_SO_RETRIES + 2):  # +1 for initial attempt
        upstream = await client.request(...)
        _write_audit(..., schema_name=_schema_name, retry_attempt=attempt)
        if upstream.status_code != 200: break
        resp_json = json.loads(upstream.content)
        so_error = validate_structured_output_response(resp_json, _schema)
        if so_error is None: break  # pass — return upstream
        if attempt > MAX_SO_RETRIES:
            return Response(schema_conformance_failure_body, 422)
    return Response(upstream.content, ...)
```

**`_validate_structured_output(body, request) -> tuple[bytes, str, dict, str] | Response`**:
Bridges HTTP layer → pure validation (mirrors `_validate_vision` / `_validate_function_calling`).

**Audit log** (`_write_audit`): add two new optional fields:
- `"schema_name"`: str (schema `name` identifier, empty string for non-SO requests)
- `"so_retry_count"`: int (0 for non-SO or successful first attempt)

**`_normalise_503`**: unchanged.

**`MAX_SO_RETRIES`** module-level constant: `int(os.environ.get("STRUCTURED_OUTPUT_MAX_RETRIES", "3"))`

#### `services/litellm/config.yaml` changes

Add `json_schema` to `capabilities` for the following models:

| Model | Provider | Mode |
|---|---|---|
| `gpt-4o` | openai | native |
| `gpt-4o-mini` | openai | native |
| `gpt-4.1` | openai | native |
| `o4-mini` | openai | native |
| `claude-sonnet` | anthropic | native |
| `claude-haiku` | anthropic | native |
| `gemini-pro` | google | prompt-based |
| `gemini-flash` | google | prompt-based |
| `command-r-plus` | cohere | prompt-based |

The capability gate uses `"json_schema" in capabilities` — `StructuredOutputCapabilityCache.load()` distinguishes native from prompt-based by checking `provider` in the model_info response.

#### `tests/contract/test_structured_output.py` (new)

Tests covering all ACs from the spec:
- `test_valid_schema_returns_conforming_json` — SC-001: 10 consecutive calls all pass
- `test_invalid_json_schema_rejected_before_inference` — SC-002: 422 with schema error, no upstream call
- `test_schema_conformance_failure_has_distinct_error_type` — SC-003: error.type == `schema_conformance_failure`
- `test_streaming_rejected_for_structured_output` — FR-008: stream + json_schema → 400
- `test_model_without_json_schema_capability_rejected` — FR-001: unsupported model → 400
- `test_schema_name_required` — FR-001: missing name → 400
- `test_schema_field_required` — FR-001: missing schema → 400
- `test_non_so_request_unaffected` — FR-008: standard request unchanged
