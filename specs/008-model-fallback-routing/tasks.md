# Tasks: Automatic Model Fallback Routing

**Input**: Design documents from `specs/008-model-fallback-routing/`

**Prerequisites**: plan.md ✓ | spec.md ✓ | research.md ✓ | data-model.md ✓ | contracts/ ✓ | quickstart.md ✓

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks in same phase)
- **[Story]**: Which user story this task belongs to (US1–US4)
- Exact file paths included in every task description

---

## Phase 1: Setup

**Purpose**: Create the two new files needed before any foundational or story work can begin.

- [X] T001 Create services/litellm/fallback_callbacks.py with module docstring, `CustomLogger` import, and empty `FallbackSpanCallback(CustomLogger)` class stub (no method bodies yet)
- [X] T002 [P] Add `./services/litellm/fallback_callbacks.py:/app/fallback_callbacks.py:ro` volume mount to the `litellm` service in docker-compose.yml alongside the existing `config.yaml` mount

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: All LiteLLM config changes that every user story depends on. MUST complete before any US phase begins.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete. All three tasks touch `services/litellm/config.yaml` and must run sequentially.

- [X] T003 Extend `litellm_settings.fallbacks` in services/litellm/config.yaml to cover all 9 chat models: add chains for `gpt-4.1` → `[claude-sonnet, gemini-pro]`, `o4-mini` → `[claude-haiku, gemini-flash]`, `command-r-plus` → `[gpt-4o, claude-sonnet]`, `gemini-pro` → `[gpt-4o, claude-sonnet]`, `claude-haiku` → `[gpt-4o-mini, gemini-flash]`; keep existing four chains unchanged
- [X] T004 Add `num_retries: 2`, `allowed_fails: 3`, and `cooldown_time: 60` to `litellm_settings` in services/litellm/config.yaml immediately after the existing `fallbacks` block
- [X] T005 Append `fallback_callbacks.FallbackSpanCallback` to `litellm_settings.callbacks` list in services/litellm/config.yaml (list must remain: `[arize_phoenix, langfuse, prometheus, fallback_callbacks.FallbackSpanCallback]`)

**Checkpoint**: LiteLLM config is complete. `make restart svc=litellm` should start cleanly with full fallback chains, retry/cooldown settings, and callback registered.

---

## Phase 3: User Story 1 — Silent Fallback on Provider Failure (Priority: P1) 🎯 MVP

**Goal**: When the primary model fails, the platform retries on the first available fallback and returns HTTP 200. The caller sees no error. The `model` field in the response identifies the fallback model.

**Independent Test**: Set `OPENAI_API_KEY` to an invalid value in `.env`, restart litellm, send `POST /v1/chat/completions` with `model: gpt-4o`. Expect HTTP 200 with `.model` = `claude-sonnet` (or `gemini-pro` if claude-sonnet also fails).

- [X] T006 [US1] Implement `FallbackSpanCallback.log_success_event` in services/litellm/fallback_callbacks.py: inspect `kwargs.get("metadata", {})` for fallback indicators; call `span.set_attribute("llm.fallback.triggered", True/False)` on the active OpenTelemetry span; set `llm.model.requested` to the original model from `kwargs["metadata"].get("model_group", kwargs["model"])`; set `llm.fallback.attempt_count` to the number of attempts from kwargs metadata; import `opentelemetry.trace` to get the current span
- [X] T007 [US1] Implement `FallbackSpanCallback.log_failure_event` in services/litellm/fallback_callbacks.py: derive `llm.fallback.reason` from the exception type (`ContextWindowExceededError` → `context_overflow`; `Timeout` → `timeout`; all others → `provider_error`); set the attribute on the active span for the failed attempt
- [X] T008 [P] [US1] Write `test_silent_fallback_returns_200` in tests/contract/test_fallback_routing.py: mock or configure the primary model to return a provider error; assert HTTP 200; assert `choices[0].message.content` is non-empty; assert `model` field is not the originally requested model
- [X] T009 [P] [US1] Write `test_fallback_model_identified_in_response` in tests/contract/test_fallback_routing.py: assert that when a fallback serves the request, `response.json()["model"]` matches the fallback model alias from the configured chain, not the originally requested model

**Checkpoint**: HTTP 200 returned from fallback model. Response `model` field reflects fallback. Phoenix span has `llm.fallback.triggered=True` visible in trace view.

---

## Phase 4: User Story 2 — 503 When All Fallbacks Exhausted (Priority: P1)

**Goal**: When the primary model and every configured fallback are all unavailable, HTTP 503 is returned with the platform's structured error schema: `{"error": "all_fallbacks_exhausted", "message": "...", "detail": {"requested_model": "...", "models_attempted": [...], "failure_reasons": {...}}}`.

**Independent Test**: Set all provider API keys to invalid values. Send `POST /v1/chat/completions`. Expect HTTP 503 with `Content-Type: application/json` and body containing `error == "all_fallbacks_exhausted"`.

- [X] T010 [US2] Add 503 response normalisation to the LiteLLM proxy handler in services/guardrails/main.py: when the upstream response status is 503, parse LiteLLM's native error body, extract model info from the exception detail, and rewrite the response body as `{"error": "all_fallbacks_exhausted", "message": "All models in the fallback chain are unavailable.", "detail": {"requested_model": <from request>, "models_attempted": <list>, "failure_reasons": <dict>}}`; preserve the 503 status code; set `Content-Type: application/json`
- [X] T011 [P] [US2] Write `test_503_when_all_fallbacks_exhausted` in tests/contract/test_fallback_routing.py: configure all models in a chain as unavailable; assert HTTP 503; assert `response.json()["error"] == "all_fallbacks_exhausted"`
- [X] T012 [P] [US2] Write `test_503_body_follows_platform_schema` in tests/contract/test_fallback_routing.py: assert the 503 body contains keys `error`, `message`, `detail`; assert `detail` contains `requested_model` (string), `models_attempted` (non-empty list), `failure_reasons` (dict with at least one entry)

**Checkpoint**: HTTP 503 with platform-compliant error schema. `error` field is `"all_fallbacks_exhausted"`. `detail` includes at least `requested_model`.

---

## Phase 5: User Story 3 — Context Window Overflow Routed to Larger Alternative (Priority: P1)

**Goal**: When a request's token count exceeds the primary model's context window, LiteLLM routes to the configured `context_window_fallbacks` target and returns HTTP 200. The `model` field identifies the overflow model.

**Independent Test**: Send `POST /v1/chat/completions` with `model: gpt-4o` and a `messages` array containing ~130 k tokens of content (exceeds gpt-4o's 128 k limit). Expect HTTP 200 with `.model == "gpt-4.1"`.

- [X] T013 [US3] Add `context_window_fallbacks` to `litellm_settings` in services/litellm/config.yaml with the six model mappings from data-model.md: `gpt-4o` → `gpt-4.1`, `gpt-4o-mini` → `gpt-4.1`, `o4-mini` → `gpt-4.1`, `claude-sonnet` → `gemini-pro`, `claude-haiku` → `gemini-pro`, `command-r-plus` → `gpt-4.1`; place immediately after the `fallbacks` block and before `num_retries`
- [X] T014 [P] [US3] Write `test_context_overflow_routes_to_larger_model` in tests/contract/test_fallback_routing.py: construct a payload with messages totalling > 128 k tokens; send with `model: gpt-4o`; assert HTTP 200; assert `response.json()["model"] == "gpt-4.1"`
- [X] T015 [P] [US3] Write `test_within_context_window_uses_primary` in tests/contract/test_fallback_routing.py: send a short request (< 1 k tokens) with `model: gpt-4o` when OpenAI key is valid; assert `response.json()["model"] == "gpt-4o"` (no overflow routing)

**Checkpoint**: Context overflow requests serve from `gpt-4.1`. Normal-length requests stay on `gpt-4o`. Both return HTTP 200.

---

## Phase 6: User Story 4 — Fallback Chains Are Operator-Configurable Per Model (Priority: P2)

**Goal**: The platform operator can add, remove, or reorder fallback entries in `services/litellm/config.yaml` and restart LiteLLM to apply changes — no code deployment required. A model with no configured chain returns 503 immediately on failure.

**Independent Test**: Remove one model from a fallback chain in config.yaml, restart litellm, trigger a primary failure, and verify the removed model is not attempted. Add it back, restart, verify it is attempted again.

- [X] T016 [US4] Extend scripts/smoke-test.sh with three fallback probes: (1) verify `model` field is present and non-empty in a 200 response to `POST /v1/chat/completions`; (2) verify HTTP 503 is returned and body contains `"all_fallbacks_exhausted"` when a sentinel model with invalid provider key and exhausted chain is used; (3) add a comment block explaining the config-reload operator workflow (`make restart svc=litellm` applies chain changes)
- [X] T017 [P] [US4] Write `test_model_with_no_chain_returns_503_immediately` in tests/contract/test_fallback_routing.py: use an embedding model endpoint (`model: text-embedding-3-small`) with an invalid provider key; assert HTTP 503 is returned immediately (no fallback attempted — no chain configured per FR-010)

**Checkpoint**: Smoke test passes three fallback probes. Config-change workflow is documented in smoke-test.sh comments. No-chain model returns 503 immediately.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Validation, regression check, and memory verification.

- [ ] T018 Run `make smoke` and verify all three new fallback probes in scripts/smoke-test.sh pass (`[PASS]` for model field check, 503 schema check, and model-field non-empty check)
- [ ] T019 [P] Run `pytest tests/contract/test_fallback_routing.py -v` and resolve any assertion or fixture failures (tests that require a live invalid-key setup may be skipped with `@pytest.mark.skip` and a comment explaining the prerequisite)
- [ ] T020 [P] Run `make stats` with the core profile running and verify total memory stays within the ~620 MB core profile budget (fallback routing adds no new containers)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — T001 and T002 can start immediately and run in parallel
- **Foundational (Phase 2)**: Depends on T001 completing (T005 references the callback file); T003 → T004 → T005 are sequential (same file)
- **US1 (Phase 3)**: Depends on Phase 2 completion; T006 → T007 sequential (same file); T008 and T009 can run in parallel with T006/T007
- **US2 (Phase 4)**: Depends on Phase 2 completion; T010 independent of Phase 3; T011 and T012 parallel
- **US3 (Phase 5)**: Depends on Phase 2 completion; T013 → T014/T015 (T014, T015 parallel after T013)
- **US4 (Phase 6)**: Depends on Phases 3–5 completion (smoke test covers full chain)
- **Polish (Phase 7)**: Depends on Phase 6 completion

### User Story Dependencies

- **US1**: Can start after Phase 2 — no dependency on US2, US3, US4
- **US2**: Can start after Phase 2 — depends on Phase 2 config (fallback chains must be defined to exhaust)
- **US3**: Can start after Phase 2 — independent of US1 and US2
- **US4**: Depends on US1 + US2 + US3 being complete (smoke test validates the full chain)

### Parallel Opportunities

- **Phase 1**: T001 ∥ T002
- **Phase 3**: T008 ∥ T009 (can write contract tests while implementing callback)
- **Phase 4**: T011 ∥ T012
- **Phase 5**: T014 ∥ T015 (after T013)
- **Phase 6**: T017 can run in parallel with T016
- **Phase 7**: T019 ∥ T020

---

## Parallel Example: Phase 3 (US1)

```bash
# T006 and T007 are sequential (same file) — implement callback first
# Then T008 and T009 can be written in parallel (both in test file, different test functions):
Task A: "Write test_silent_fallback_returns_200 in tests/contract/test_fallback_routing.py"
Task B: "Write test_fallback_model_identified_in_response in tests/contract/test_fallback_routing.py"
```

---

## Implementation Strategy

### MVP First (US1 + US2 — Core Reliability)

1. Complete Phase 1: Setup (T001, T002)
2. Complete Phase 2: Foundational config (T003 → T004 → T005)
3. Complete Phase 3: US1 Silent Fallback (T006 → T007, T008 ∥ T009)
4. Complete Phase 4: US2 503 Exhausted (T010, T011 ∥ T012)
5. **STOP and VALIDATE**: `make smoke` + manual quickstart Scenarios 1–3
6. US2 completes the core reliability guarantee — ship if needed

### Incremental Delivery

1. Setup + Foundational → fallback config active
2. US1 → silent fallback working, Phoenix traces show `llm.fallback.triggered`
3. US2 → 503 exhaustion with platform schema working
4. US3 → context overflow routing active
5. US4 → smoke test extended, operator workflow documented

### Notes

- T003–T005 all modify `services/litellm/config.yaml` — do NOT attempt to run in parallel
- Contract tests (T008, T009, T011, T012, T014, T015, T017) requiring a provider failure state should use `@pytest.mark.skipif` with an env var guard (e.g. `LITELLM_FALLBACK_TEST_MODE=1`) rather than hardcoding invalid keys
- After T005, always run `make restart svc=litellm` and verify no startup errors before proceeding to story phases
- Commit after each phase checkpoint
