# Data Model: Structured JSON Output

**Branch**: `020-structured-json-output` | **Date**: 2026-06-07

All entities are runtime-only — none are persisted to PostgreSQL, Redis, Loki, or any other store. Constitution §II applies: schema content and response content are never stored.

---

## Entity 1 — StructuredOutputRequest

The inbound chat-completion request body augmented with a `response_format` object.

| Field | Type | Constraints |
|---|---|---|
| `model` | `str` | Required. Must be in `StructuredOutputCapabilityCache.all_json_schema_models`. |
| `messages` | `list[dict]` | Required. Existing field — unchanged by this feature. |
| `response_format.type` | `str` | Required. Must equal `"json_schema"` to activate this feature. |
| `response_format.name` | `str` | Required. Non-empty string. Used as Phoenix span attribute and in error responses. |
| `response_format.strict` | `bool` | Optional. Defaults to `true`. No lenient mode — `false` is treated as `true`. |
| `response_format.schema` | `dict` | Required. JSON Schema object (draft-07 or later). Must pass meta-schema validation. |
| `stream` | `bool` | Must be absent or `false` when `response_format.type == "json_schema"`. |
| `metadata.schema_name` | `str` | Injected by Guardrails pre-proxy. Carries the `response_format.name` value. |
| `metadata.schema_hash` | `str` | Injected by Guardrails pre-proxy. 12-hex SHA-256 of sorted schema JSON. |

**State transitions**:
```
Received → Pre-proxy validation → [REJECTED 400/422] or [Forwarded to LiteLLM]
                                                                ↓
                                                    Post-proxy validation
                                                    ↓              ↓
                                               [PASS]         [FAIL → retry]
                                                  ↓                ↓
                                           Return 200     Retry ≤ MAX_RETRIES
                                                                    ↓
                                                          [PASS] or [FAIL → 422]
```

---

## Entity 2 — ValidationResult

The outcome of validating a single model response against the caller-provided schema. Not persisted; lives only in the retry loop.

| Field | Type | Values |
|---|---|---|
| `status` | `str` | `"pass"` · `"retry"` · `"fail"` |
| `attempt` | `int` | 1-based attempt number (1 = first try, up to `MAX_RETRIES + 1`) |
| `reason` | `str \| None` | Error description on non-pass; `None` on pass |
| `schema_hash` | `str` | 12-hex digest for metric attribution |
| `schema_name` | `str` | Schema identifier for Phoenix span and error response |

**Status rules**:
- `pass` — `choices[0].message.content` is valid JSON and passes `jsonschema.validate()`
- `retry` — validation failed and `attempt ≤ MAX_RETRIES`; Guardrails issues another request to LiteLLM
- `fail` — validation failed and `attempt > MAX_RETRIES`; Guardrails returns `422`

---

## Entity 3 — StructuredOutputCapabilityCache

In-memory startup state. Loaded once during FastAPI lifespan from LiteLLM's `/model/info` endpoint.

| Field | Type | Description |
|---|---|---|
| `native_model_names` | `frozenset[str]` | Models where LiteLLM passes `response_format` natively (OpenAI + Anthropic providers). |
| `prompt_model_names` | `frozenset[str]` | Models where LiteLLM uses system-prompt enforcement (Google + Cohere providers). |
| `all_json_schema_models` | `frozenset[str]` | Union of the two sets; used by the capability gate. |

**Population logic**: For each model in `/model/info`, include it if `"json_schema" in model_info.capabilities`. Determine set membership by `model_info.provider` (`"openai"` / `"anthropic"` → native; `"google"` / `"cohere"` → prompt-based).

**Initial values** (from `config.yaml` after this feature lands):

| Model | Set |
|---|---|
| `gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `o4-mini` | native |
| `claude-sonnet`, `claude-haiku` | native |
| `gemini-pro`, `gemini-flash` | prompt-based |
| `command-r-plus` | prompt-based |

---

## Entity 4 — SchemaHash

A short, deterministic identifier for a given JSON Schema object.

| Field | Type | Description |
|---|---|---|
| `value` | `str` | `hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:12]` |

**Purpose**: Metric counters (pass/retry/fail) are labelled by `schema_hash` and `model_name`. This allows operators to identify which schema + model combination is producing retries without storing the schema itself.

**Invariants**:
- Identical schemas always produce the same hash (deterministic sort)
- Different schemas are extremely unlikely to collide (12 hex chars = 48 bits)
- The hash is never used as a cache key — only for metric attribution

---

## Audit Log Extension

Two new fields are added to the existing `guardrails.audit` JSON log entry (see `_write_audit` in `main.py`). Existing fields are unchanged.

| New Field | Type | Value |
|---|---|---|
| `schema_name` | `str` | `response_format.name` for SO requests; `""` for all others |
| `so_retry_count` | `int` | Number of retries that occurred (0 = no retries, either non-SO or first-attempt pass) |

**Constitution compliance**: `schema_name` is a user-chosen identifier string (e.g., `"invoice_schema"`), not prompt or response content. It is metadata and is permitted in the audit trail per constitution §6.3.
