# Tasks: Structured JSON Output

**Input**: Design documents from `specs/020-structured-json-output/`

**Prerequisites**: plan.md ✓ spec.md ✓ research.md ✓ data-model.md ✓ contracts/ ✓ quickstart.md ✓

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- File paths are relative to repository root

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add the one new dependency and declare `json_schema` capability on all supporting models. Both tasks are independent and can land before any code is written.

- [x] T001 Add `jsonschema>=4.17.0` to `services/guardrails/requirements.txt`
- [x] T002 Add `json_schema` to the `capabilities` list for `gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `o4-mini`, `claude-sonnet`, `claude-haiku`, `gemini-pro`, `gemini-flash`, `command-r-plus` in `services/litellm/config.yaml`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create `structured_output.py` with the capability cache and the two detection helpers that every user-story phase depends on.

**⚠️ CRITICAL**: Phases 3, 4, and 5 cannot begin until this phase is complete.

- [x] T003 Create `services/guardrails/structured_output.py` — module docstring, `from __future__ import annotations`, all imports (`json`, `hashlib`, `os`, `dataclasses`, `httpx`, `jsonschema`)
- [x] T004 [P] Implement `StructuredOutputCapabilityCache` dataclass in `services/guardrails/structured_output.py` — fields: `native_model_names: frozenset[str]`, `prompt_model_names: frozenset[str]`, `all_json_schema_models: frozenset[str]`; `load()` async method reads `/model/info`, includes model if `"json_schema" in capabilities`, sets native vs prompt-based by `provider` field (`openai`/`anthropic` → native; `google`/`cohere` → prompt); raises `RuntimeError` if LiteLLM unreachable (fail-fast)
- [x] T005 [P] Implement `has_structured_output_request(body: dict) -> bool` (returns `True` iff `body.get("response_format", {}).get("type") == "json_schema"`) and `compute_schema_hash(schema: dict) -> str` (`hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:12]`) in `services/guardrails/structured_output.py`

**Checkpoint**: `structured_output.py` exists with cache + two pure helpers. All user story phases can now proceed.

---

## Phase 3: User Story 1 — Schema-Constrained Inference Request (Priority: P1) 🎯 MVP

**Goal**: A valid structured output request passes through the full gateway chain, Guardrails injects `schema_name` metadata for Phoenix, the model returns a JSON string, Guardrails validates it against the caller schema, and all 10 consecutive calls pass.

**Independent Test**: Run the 10-call loop from `specs/020-structured-json-output/quickstart.md` (SC-001). All 10 `choices[0].message.content` values parse as JSON and validate against the invoice schema.

### Implementation for User Story 1

- [x] T006 [US1] Implement `inject_schema_metadata(body: dict, schema_name: str, schema_hash: str) -> dict` in `services/guardrails/structured_output.py` — deep-copies the body, calls `body.setdefault("metadata", {})` then sets `metadata["schema_name"] = schema_name` and `metadata["schema_hash"] = schema_hash`, returns modified dict
- [x] T007 [US1] Implement `validate_structured_output_response(response_body: dict, schema: dict) -> tuple[dict, int] | None` in `services/guardrails/structured_output.py` — extracts `choices[0].message.content`, parses as JSON (failure → `(502_envelope, 502)`), runs `jsonschema.validate(parsed, schema)` (failure → returns `None` to signal retry needed); returns `None` on pass
- [x] T008 [US1] Add `MAX_SO_RETRIES: int = int(os.environ.get("STRUCTURED_OUTPUT_MAX_RETRIES", "3"))` module constant and update the import/try block for `structured_output` (mirroring the `vision`/`function_calling` try/except import pattern) in `services/guardrails/main.py`
- [x] T009 [US1] Add `StructuredOutputCapabilityCache` load to `_lifespan()` in `services/guardrails/main.py` — create cache, call `await so_cache.load()`, store as `application.state.so_cache`, log `"StructuredOutputCapabilityCache loaded: %d native, %d prompt-based models"`
- [x] T010 [US1] Implement `_validate_structured_output(body: bytes, request: Request) -> tuple[bytes, str, dict, str] | Response` in `services/guardrails/main.py` — parses body, calls `has_structured_output_request()`; if not SO returns `(body, "", {}, "")`; reads `app.state.so_cache`; checks `stream: true` → 400; checks model in `cache.all_json_schema_models` → 400; extracts `schema_name`, `schema`, `schema_hash`; calls `inject_schema_metadata()`; returns `(new_body_bytes, schema_name, schema, schema_hash)`
- [x] T011 [US1] Extend `_write_audit()` in `services/guardrails/main.py` — add `schema_name: str = ""` and `so_retry_count: int = 0` keyword parameters; add both fields to the `entry` dict; update the single call-site in `proxy()` to pass these values
- [x] T012 [US1] Wire SO path into `proxy()` in `services/guardrails/main.py` — after the FC gate, call `_validate_structured_output()` and return early if it returns a `Response`; if SO request, replace the single `client.request()` call with a retry for-loop (`for attempt in range(1, MAX_SO_RETRIES + 2)`): call LiteLLM, write audit with `so_retry_count=attempt-1`, on 200 call `validate_structured_output_response()`, break on pass, on exhaustion return 422 placeholder; non-SO requests use the existing single-call path unchanged

### Contract tests for User Story 1

- [x] T013 [P] [US1] Write `test_valid_schema_returns_conforming_json` in `tests/contract/test_structured_output.py` — calls the endpoint 10 consecutive times with the invoice schema against `gpt-4o-mini`; asserts every response is HTTP 200, `content` is a parseable JSON string, and parsed object validates against the schema (SC-001)
- [x] T014 [P] [US1] Write `test_non_so_request_unaffected` and `test_streaming_rejected_for_structured_output` in `tests/contract/test_structured_output.py` — former: request without `response_format` returns 200 normally; latter: `stream: true` + `json_schema` returns 400 with `error.type: structured_output_streaming_not_supported`

**Checkpoint**: US1 fully functional. 10/10 consecutive structured output calls pass. Non-SO requests unaffected. Streaming blocked.

---

## Phase 4: User Story 2 — Invalid Schema Rejected Before Inference (Priority: P2)

**Goal**: Requests with a missing `name`, missing `schema`, a model that doesn't support `json_schema`, or a schema that fails draft-07 meta-schema validation are all rejected with descriptive 4xx errors before any model call is made.

**Independent Test**: Run the SC-002 command from `quickstart.md` — submit a request with `"type": "bogus_type"` in the schema; verify HTTP 422 is returned in under 100 ms (no LiteLLM latency in the trace).

### Implementation for User Story 2

- [x] T015 [US2] Implement `validate_structured_output_request(body: dict, cache: StructuredOutputCapabilityCache) -> tuple[dict, int] | None` in `services/guardrails/structured_output.py` — validation order: (1) `stream: true` → 400 `structured_output_streaming_not_supported`; (2) `response_format.name` absent/empty → 400 `invalid_structured_output_request`; (3) `response_format.schema` absent/non-dict → 400 `invalid_structured_output_request`; (4) model not in `cache.all_json_schema_models` → 400 `structured_output_model_required` listing valid models; (5) `jsonschema.Draft7Validator.check_schema(schema)` raises → 422 `invalid_json_schema` with the validator error message; all failures use OpenAI envelope
- [x] T016 [US2] Wire `validate_structured_output_request()` into `_validate_structured_output()` in `services/guardrails/main.py` — call it before the streaming/model checks that were added in T010; because T015 now covers streaming and model cap checks, remove the inline duplicates from T010 so validation is not repeated

### Contract tests for User Story 2

- [x] T017 [P] [US2] Write `test_invalid_json_schema_rejected_before_inference` and `test_model_without_json_schema_capability_rejected` in `tests/contract/test_structured_output.py` — former: schema with `"type": "bogus_type"` → 422 `invalid_json_schema`; latter: a model name not in capabilities → 400 `structured_output_model_required`
- [x] T018 [P] [US2] Write `test_schema_name_required` and `test_schema_field_required` in `tests/contract/test_structured_output.py` — former: `response_format` with no `name` → 400 `invalid_structured_output_request`; latter: `response_format` with no `schema` → 400 `invalid_structured_output_request`

**Checkpoint**: US1 and US2 both independently functional. Invalid requests are rejected in < 100 ms; valid requests still return conforming JSON.

---

## Phase 5: User Story 3 — Schema Conformance Failure as Distinct Error (Priority: P3)

**Goal**: When all retries are exhausted, the response is HTTP 422 with `error.type: schema_conformance_failure`, a `retry_count` field equal to the number of retries attempted, and a `schema_name` field echoing the caller's `response_format.name`. This error type is lexically distinct from all other platform error types.

**Independent Test**: Run the SC-003 command from `quickstart.md` — submit a schema with `minimum: 100, maximum: 5`; verify HTTP 422, `error.type == "schema_conformance_failure"`, `retry_count == 3`, and `schema_name` matches the submitted name.

### Implementation for User Story 3

- [x] T019 [US3] Implement the complete `422 schema_conformance_failure` response body in the retry loop in `services/guardrails/main.py` — replace the placeholder 422 from T012 with `{"error": {"message": "Model response did not conform to the provided JSON schema after {n} attempt(s).", "type": "schema_conformance_failure", "code": "schema_conformance_failure"}, "retry_count": MAX_SO_RETRIES, "schema_name": schema_name}` returned as a `Response(status_code=422, media_type="application/json")`

### Contract tests for User Story 3

- [x] T020 [P] [US3] Write `test_schema_conformance_failure_has_distinct_error_type` in `tests/contract/test_structured_output.py` — mocks LiteLLM to always return non-conforming JSON; asserts HTTP 422, `error["type"] == "schema_conformance_failure"`, `retry_count == 3`, `schema_name` echoes the submitted name; also asserts this type is different from `"all_fallbacks_exhausted"` and `"upstream_error"`

**Checkpoint**: All three user stories complete. Error taxonomy is unambiguous and testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Smoke test probes, linting, and end-to-end validation against the running stack.

- [x] T021 [P] Add `[020]` structured output probe cases to `scripts/smoke-test.sh` — at minimum: (a) valid SO request to `gpt-4o-mini` → assert HTTP 200 and parseable JSON content; (b) invalid schema → assert HTTP 422; (c) missing name → assert HTTP 400
- [x] T022 Run `ruff check services/guardrails/` and `mypy services/guardrails/ --ignore-missing-imports` from repo root; fix any type annotation gaps or lint warnings in `structured_output.py` and the modified sections of `main.py`
- [x] T023 Start core stack with `make up-core && make seed-kong`, run `make smoke`, verify `[020]` probes all pass, run `make stats` to confirm memory remains within `core` profile budget (~620 MB)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately; T001 and T002 are parallel
- **Phase 2 (Foundational)**: Depends on Phase 1 — T003 must complete before T004 and T005; T004 and T005 are parallel once T003 is done
- **Phase 3 (US1)**: Depends on Phase 2 completion — T006 → T007 → T008/T009/T010/T011 (parallel) → T012 → T013/T014 (parallel)
- **Phase 4 (US2)**: Depends on Phase 2 completion — T015 → T016 → T017/T018 (parallel); can run concurrently with Phase 3
- **Phase 5 (US3)**: Depends on T012 (retry loop exists) — T019 → T020
- **Phase 6 (Polish)**: Depends on all story phases complete

### User Story Dependencies

- **US1 (P1)**: Depends on Foundational — no dependency on US2 or US3
- **US2 (P2)**: Depends on Foundational — refines the bridge function introduced in US1 (T016 consolidates T010's inline checks)
- **US3 (P3)**: Depends on T012 (the retry loop shell) being complete

### Within Each User Story

- Module-level helpers before integration tasks
- Capability cache load before gate wiring
- Gate wiring before proxy integration
- Contract tests can be written in parallel with implementation (they exercise the contract boundary)

---

## Parallel Opportunities

```bash
# Phase 1 (both in different files):
T001  services/guardrails/requirements.txt
T002  services/litellm/config.yaml

# Phase 2 (after T003 lands):
T004  StructuredOutputCapabilityCache
T005  has_structured_output_request + compute_schema_hash

# Phase 3 + Phase 4 (after Phase 2 — different files or non-overlapping sections):
T006  structured_output.py: inject_schema_metadata
T007  structured_output.py: validate_structured_output_response

T008  main.py: constant + import
T009  main.py: lifespan load
T010  main.py: bridge function
T011  main.py: audit extension

# Contract tests (parallel within each story):
T013 + T014  US1 tests
T017 + T018  US2 tests
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1: Setup (T001, T002)
2. Phase 2: Foundational (T003 → T004/T005)
3. Phase 3: US1 (T006 → T007 → T008–T011 → T012 → T013/T014)
4. **STOP and VALIDATE**: Run the 10-call loop from quickstart.md — all 10 pass
5. Non-SO requests unaffected ✓ Streaming blocked ✓

### Incremental Delivery

1. MVP (US1) → 10/10 conformance guarantee live
2. Add US2 → Pre-inference rejection live (cheaper error path, no wasted model calls)
3. Add US3 → Error taxonomy complete (callers can handle `schema_conformance_failure` programmatically)
4. Polish → Smoke tests green, lint clean

---

## Notes

- `[P]` tasks touch different files or non-overlapping code sections within the same file
- T016 refactors T010's inline streaming/model-cap checks into `validate_structured_output_request()` — this is intentional consolidation, not duplication
- Constitution §II: schema content (`response_format.schema` object) and response content (`choices[0].message.content`) are never logged — only `schema_name` (identifier) and `so_retry_count` (int) appear in the audit entry
- `strict: false` is treated as `true` — no special handling needed; the flag is passed as-is to providers which default to strict behavior
