# Tasks: Function Calling Support

**Input**: Design documents from `specs/019-function-calling/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/ ✅

**Tests**: Contract tests are included (plan.md specifies `tests/contract/test_function_calling.py`).

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no shared-state dependencies)
- **[Story]**: Maps to user story from spec.md (US1–US4)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: No new infrastructure required. `config.yaml` already declares `function-calling` for all relevant models. No Docker changes needed. Phase 1 is a single verification task.

- [x] T001 Verify `function-calling` capability is declared for all expected models in `services/litellm/config.yaml`: confirm `gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `o4-mini`, `claude-sonnet`, `gemini-pro`, `gemini-flash`, `command-r-plus` each have `function-calling` in their `capabilities` list; confirm `claude-haiku` and embedding models do NOT

**Checkpoint**: Config verified — no changes needed → foundational work can begin

---

## Phase 2: Foundational (Blocking All User Stories)

**Purpose**: `FunctionCallingCapabilityCache` + lifespan wiring + audit log extension. Must be complete before any user story gate can be tested.

- [x] T002 [P] Create `services/guardrails/function_calling.py` with `FunctionCallingCapabilityCache` class: async `load()` calls `GET {LITELLM_BASE_URL}/model/info` with `Authorization: Bearer {LITELLM_MASTER_KEY}`, extracts model names where `"function-calling" in model_info.capabilities`, stores as `frozenset[str]`; raises `RuntimeError` on unreachable endpoint (mirrors `VisionCapabilityCache` pattern exactly)
- [x] T003 [P] Add `has_tools(body: dict[str, Any]) -> bool` function to `services/guardrails/function_calling.py`: returns `True` if `body.get("tools")` is a list with at least one entry
- [x] T004 Wire `FunctionCallingCapabilityCache` into the FastAPI lifespan in `services/guardrails/main.py`: load alongside `VisionCapabilityCache`, expose as `app.state.fc_cache`; service must not start if FC cache load fails
- [x] T005 Add `tool_count: int` field to `_write_audit()` in `services/guardrails/main.py`: count of entries in the request `tools` array (0 for non-FC requests); never log tool names or argument values

**Checkpoint**: Guardrails starts with both caches loaded, `tool_count` appears in audit log → user story phases can begin

---

## Phase 3: User Story 1 — Auto Tool Selection (Priority: P1) 🎯 MVP

**Goal**: A caller sends `tool_choice: "auto"` with a matching message. The gateway validates the request, forwards to LiteLLM, and returns the model's tool call response with `finish_reason: "tool_calls"` and valid JSON `arguments`. A non-matching message returns plain text. The gateway also validates the response-side `arguments` JSON (FR-016).

**Independent Test**:
```bash
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","tool_choice":"auto","tools":[{"type":"function","function":{"name":"get_weather","description":"Get weather","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}],"messages":[{"role":"user","content":"What is the weather in London?"}]}' \
  | jq '{finish_reason:.choices[0].finish_reason,fn:.choices[0].message.tool_calls[0].function.name,args_valid:(.choices[0].message.tool_calls[0].function.arguments|fromjson|type)}'
# Expected: finish_reason:"tool_calls", fn:"get_weather", args_valid not null
```

### Implementation for User Story 1

- [x] T006 [P] [US1] Add `validate_function_calling_request(body: dict[str, Any], fc_models: frozenset[str]) -> tuple[dict[str, Any], int] | None` skeleton to `services/guardrails/function_calling.py`: validates in order — (1) `stream: true` + non-empty `tools` → OpenAI envelope 400 `function_calling_streaming_not_supported`; (2) model not in `fc_models` → OpenAI envelope 400 `function_calling_model_required` naming the model and listing valid alternatives; returns `None` on pass
- [x] T007 [P] [US1] Add `validate_tool_call_response(response_body: dict[str, Any]) -> tuple[dict[str, Any], int] | None` to `services/guardrails/function_calling.py`: if `choices[0].message.tool_calls` exists, attempt `json.loads()` on each `function.arguments`; on failure return `({"error": {"message": "...", "type": "upstream_error", "code": "invalid_tool_arguments"}}, 502)`; return `None` on pass or when no tool_calls present
- [x] T008 [US1] Wire the function-calling pre-proxy gate into `_validate_function_calling(body, request)` helper in `services/guardrails/main.py`: if `has_tools(payload)` → call `validate_function_calling_request()`; on error return `Response(json.dumps(error), status_code, media_type="application/json")`; on pass return `(body, tool_count)`
- [x] T009 [US1] Wire the post-proxy response inspection into `proxy()` in `services/guardrails/main.py`: after receiving the upstream HTTP 200 response for a tools request, call `validate_tool_call_response(json.loads(upstream.content))`; on error return the 502 `Response` instead of the upstream response
- [x] T010 [US1] Add `[019]` auto-tool-selection smoke test to `scripts/smoke-test.sh` (AC-1 from plan.md): POST `gpt-4o` with `get_weather` tool + "weather in London" message; assert HTTP 200, `finish_reason: "tool_calls"`, `tool_calls[0].function.name == "get_weather"`, and `arguments` parses as valid JSON

**Checkpoint**: Auto tool call returns HTTP 200 with correct structure; non-matching message returns plain text — US1 independently functional

---

## Phase 4: User Story 2 — Forced Tool Selection (Priority: P2)

**Goal**: `tool_choice` explicitly names a function. Gateway validates the name exists in `tools`, forwards to LiteLLM, model always calls that function. `tool_choice` naming a non-existent function returns 400.

**Independent Test**:
```bash
curl -s -w "\nHTTP:%{http_code}" -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","tool_choice":{"type":"function","function":{"name":"extract_invoice"}},"tools":[{"type":"function","function":{"name":"extract_invoice","parameters":{"type":"object","properties":{"vendor":{"type":"string"},"total":{"type":"number"}},"required":["vendor","total"]}}}],"messages":[{"role":"user","content":"Invoice from Acme Corp, $1250, 2026-05-15"}]}'
# Expected: HTTP 200, tool_calls[0].function.name == "extract_invoice"
```

### Implementation for User Story 2

- [x] T011 [US2] Add `tool_choice` consistency validation to `validate_function_calling_request()` in `services/guardrails/function_calling.py`: (3) `tool_choice` set to a non-`"auto"`/`"none"` value but `tools` is empty or absent → OpenAI envelope 400 `tool_choice_requires_tools`; (4) `tool_choice` is an object with `function.name` that does not appear in any entry of the `tools` array → OpenAI envelope 400 `tool_choice_function_not_found` naming the missing function

**Checkpoint**: Forced tool returns the named function; unknown function reference returns 400 — US2 independently functional

---

## Phase 5: User Story 3 — Disable Tool Use (Priority: P2)

**Goal**: `tool_choice: "none"` with a triggering message returns plain text with `finish_reason: "stop"`. The gate must pass this through without rejection (it is valid).

**Independent Test**:
```bash
curl -s -w "\nHTTP:%{http_code}" -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","tool_choice":"none","tools":[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"city":{"type":"string"}}}}}],"messages":[{"role":"user","content":"What is the weather in Tokyo?"}]}' \
  | jq '{finish_reason:.choices[0].finish_reason,content:.choices[0].message.content,has_calls:(.choices[0].message.tool_calls!=null)}'
# Expected: finish_reason:"stop", content is non-empty string, has_calls:false
```

### Implementation for User Story 3

- [x] T012 [US3] Verify `validate_function_calling_request()` in `services/guardrails/function_calling.py` does NOT reject `tool_choice: "none"` — it is a valid value that must pass through; confirm no validation rule accidentally blocks it; add a note in the function's docstring that `"none"` skips all tool-call validation

**Checkpoint**: `tool_choice: "none"` passes the gate and returns plain text — US3 independently functional

---

## Phase 6: User Story 4 — Multi-Tool Disambiguation (Priority: P3)

**Goal**: Three unrelated tools defined. Message clearly matches one. Model returns a tool call for only that function. Message matching none → plain text.

**Independent Test**:
```bash
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","tool_choice":"auto","tools":[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"city":{"type":"string"}}}}},{"type":"function","function":{"name":"book_meeting","parameters":{"type":"object","properties":{"attendee":{"type":"string"},"time":{"type":"string"}}}}},{"type":"function","function":{"name":"calculate","parameters":{"type":"object","properties":{"expression":{"type":"string"}}}}}],"messages":[{"role":"user","content":"Book a meeting with Alice at 3pm tomorrow"}]}' \
  | jq '.choices[0].message.tool_calls[0].function.name'
# Expected: "book_meeting"
```

### Implementation for User Story 4

- [x] T013 [US4] Add tool definition structure validation to `validate_function_calling_request()` in `services/guardrails/function_calling.py`: (5) iterate `tools` array; if any entry is missing `function.name` (absent or empty string) or `function.parameters` is not a dict → OpenAI envelope 400 `invalid_tool_definition` including the zero-based `tool_index`
- [x] T014 [P] [US4] Verify `count_tools(body: dict[str, Any]) -> int` helper exists (or add it) in `services/guardrails/function_calling.py`: returns `len(body.get("tools", []))` for use in `tool_count` audit field; ensure this counts correctly across multi-tool requests

**Checkpoint**: Three-tool request routes to the correct function; definition validation rejects malformed tools — US4 independently functional

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Contract tests, non-FC model rejection smoke test, ruff/mypy quality gates.

- [x] T015 [P] Create `tests/contract/test_function_calling.py` with 9 contract test cases from plan.md: (1) auto-select matching → 200 `tool_calls`; (2) auto-select non-matching → 200 `stop`; (3) forced tool → 200 correct name; (4) `tool_choice: "none"` → 200 plain text; (5) non-FC model → 400 `function_calling_model_required`; (6) stream + tools → 400 `function_calling_streaming_not_supported`; (7) unknown tool_choice function → 400 `tool_choice_function_not_found`; (8) missing `name` in tool → 400 `invalid_tool_definition`; (9) text-only regression → 200
- [x] T016 [P] Add `[019]` non-FC model rejection smoke test to `scripts/smoke-test.sh` (AC-4 from plan.md): POST `claude-haiku` with any tool definition; assert HTTP 400, `error.code == "function_calling_model_required"`
- [x] T017 Run `ruff check services/guardrails/` — zero warnings; fix any issues in `services/guardrails/function_calling.py`
- [x] T018 Run `mypy services/guardrails/ --ignore-missing-imports` — zero errors; add missing type annotations to all function signatures in `services/guardrails/function_calling.py`
- [x] T019 Run `make smoke` with `core` profile active — confirm all pre-existing cases pass and new `[019]` cases pass; verify no regression on text-only or vision requests

---

## Dependencies & Execution Order

### Phase Dependencies

```
T001 (config verify)
    │
    ▼
T002–T005 (Foundational) — BLOCKS all user stories
    │
    ├──► T006–T010 (US1: auto select + response gate) — independently startable
    │
    ├──► T011 (US2: forced tool + unknown name) — depends on T006 (validate_fc_request established)
    │
    ├──► T012 (US3: tool_choice:none pass-through) — depends on T006
    │
    └──► T013–T014 (US4: multi-tool + definition validation) — depends on T006
         │
         ▼
    T015–T019 (Polish) — after all desired stories complete
```

### User Story Dependencies

- **US1 (P1)**: Starts after Phase 2. No dependency on US2/US3/US4.
- **US2 (P2)**: Extends `validate_function_calling_request()` established in T006. Start after T006.
- **US3 (P2)**: Verifies existing behaviour in `validate_function_calling_request()`. Start after T006.
- **US4 (P3)**: Adds final validation rule to `validate_function_calling_request()`. Start after T006; benefits from US2 complete.

### Within Each Phase

- T002 and T003 are parallel (independent functions in the same new file).
- T006 and T007 are parallel (request vs. response validation — independent functions).
- T008 depends on T006; T009 depends on T007.
- T015 and T016 are parallel (different files).
- T017 and T018 are parallel (ruff vs mypy — different tools).

---

## Parallel Opportunities

```bash
# Phase 2 — foundational tasks:
T002 + T003 — FunctionCallingCapabilityCache + has_tools() (independent functions, same new file)
T004 — wiring into main.py (depends on T002)

# Phase 3 — US1 setup:
T006 + T007 — request validation + response validation (independent functions)

# Phase 7 — polish:
T015 + T016 — contract tests + smoke test (different files)
T017 + T018 — ruff + mypy (independent tools)
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete T001 (config verify)
2. Complete T002–T005 (Foundational)
3. Complete T006–T010 (US1)
4. **STOP and VALIDATE**: curl with `get_weather` tool + weather message → HTTP 200 with tool_calls
5. Ship if valuable on its own

### Incremental Delivery

1. T001 → T002–T005 → Foundation ready
2. T006–T010 (US1) → Auto tool selection works → Demo/Deploy
3. T011 (US2) → Forced tool + rejection → Demo/Deploy
4. T012 (US3) → tool_choice:none works → Demo/Deploy
5. T013–T014 (US4) → Multi-tool + validation → Demo/Deploy
6. T015–T019 → Polish, tests, lint clean

---

## Notes

- `[P]` tasks touch distinct code paths; no merge conflicts expected in `function_calling.py` phases.
- `validate_function_calling_request()` validation order matters — stream guard runs before capability check (same as vision). Do NOT reorder.
- `tool_choice: "none"` is NOT a rejection target — it is a valid value that passes through. T012 specifically verifies this.
- The post-proxy response inspection (T009) is unique to this feature — vision does not have an equivalent. Handle only HTTP 200 upstream responses; pass through non-200 (including 503, already handled by `_normalise_503`).
- Commit after each checkpoint (end of Phase 2, end of each US phase).
- `make smoke` must pass at each checkpoint — do not proceed if smoke fails.
