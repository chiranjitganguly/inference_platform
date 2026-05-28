# Tasks: Chat Completion with Observability

**Input**: Design documents from `specs/005-chat-completion-otel-langfuse/`

**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/openapi.yaml ✓, quickstart.md ✓

**Tests**: Included — plan.md explicitly calls for a chat-completion contract test and smoke test extension.

**Organization**: Tasks grouped by user story. US1–US4 all P1; run sequentially (US1 → US2 → US3 → US4) since the contract test file is shared.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (touches different files, no dependency on incomplete tasks)
- **[Story]**: User story (US1–US4)
- Exact file paths included in every task description

---

## Phase 1: Setup (Environment & Prerequisites)

**Purpose**: Verify environment is wired before any obs-profile services are added.

- [x] T001 Verify `scripts/init-db.sql` creates both `phoenix` and `langfuse` databases; add them if missing in `scripts/init-db.sql`
- [x] T002 Add all obs-profile env var names to `.env.example` (no values): `PHOENIX_SQL_DATABASE_URL`, `LANGFUSE_INIT_USER_EMAIL`, `LANGFUSE_INIT_USER_PASSWORD`, `NEXTAUTH_URL`, `NEXTAUTH_SECRET`, `SALT` — alongside the already-present `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `OTEL_EXPORTER_OTLP_ENDPOINT`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add Phoenix Arize and Langfuse to docker-compose.yml (obs profile) and configure LiteLLM metadata forwarding. MUST complete before US2 and US3 can be verified end-to-end.

**⚠️ CRITICAL**: US2 and US3 acceptance tests require obs-profile services running.

- [x] T003 Add `arize-phoenix` service to `docker-compose.yml` under `obs` profile: image `arizephoenix/phoenix:latest`, expose port `6006` (no host binding except dev inspection), env `PHOENIX_SQL_DATABASE_URL`, depends_on postgres with healthcheck
- [x] T004 Add `langfuse-server` service to `docker-compose.yml` under `obs` profile: image `langfuse/langfuse:3`, expose port `3002`, env `DATABASE_URL` (langfuse db), `NEXTAUTH_URL`, `NEXTAUTH_SECRET`, `SALT`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, depends_on postgres with healthcheck, healthcheck on `http://localhost:3002/api/public/health`
- [x] T005 [P] Add `langfuse-worker` service to `docker-compose.yml` under `obs` profile: image `langfuse/langfuse-worker:3`, same env as langfuse-server, depends_on langfuse-server with healthcheck
- [x] T006 [P] Add `phoenix_data` and `langfuse_data` named volumes to the `volumes:` block at the bottom of `docker-compose.yml`
- [x] T007 Verify `litellm` service in `docker-compose.yml` has `OTEL_EXPORTER_OTLP_ENDPOINT`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` in its `environment:` block; add any that are missing; update `LANGFUSE_HOST` value reference to `${LANGFUSE_HOST}` if not already present
- [x] T008 Add `success_callback` and `failure_callback` keys under `litellm_settings:` in `services/litellm/config.yaml` to ensure metadata (`team`, `request_id`, `prompt_name`) is forwarded to both arize_phoenix and langfuse on success; verify `callbacks: [arize_phoenix, langfuse, prometheus]` is already present (it is — no change needed)

**Checkpoint**: `make up-obs` starts all services healthy; `curl http://localhost:6006` returns Phoenix UI; `curl http://localhost:3002/api/public/health` returns 200.

---

## Phase 3: User Story 1 — Submit a Chat Completion Request (P1) 🎯 MVP

**Goal**: Verify the inference path returns a valid non-streaming HTTP 200 with `content`, `prompt_tokens`, `completion_tokens`, and `finish_reason`.

**Independent Test**: `POST /v1/chat/completions` with a valid key and a single user message returns HTTP 200 with all four required fields non-null.

### Contract Test for US1

- [x] T009 [US1] Create `tests/contract/test_chat_completions.py` with a `test_basic_chat_completion` test: POST to `/v1/chat/completions` with `model: gpt-4o-mini` and one user message; assert HTTP 200; assert `choices[0].message.content` is a non-empty string; assert `usage.prompt_tokens > 0` and `usage.completion_tokens > 0`; assert `choices[0].finish_reason` in `{"stop", "length", "content_filter"}`; assert response headers include `X-Request-ID`, `X-Platform: inference-platform`, `X-API-Version: 1`

### Implementation for US1

- [x] T010 [US1] Verify `services/kong/kong.yml` litellm-proxy route includes `POST` in its `methods:` list for path `/v1`; add `POST` if missing (it should already be there from feature 002)
- [x] T011 [US1] Add authenticated `POST /v1/chat/completions` probe to `scripts/smoke-test.sh` inside the `if [[ -n "$SMOKE_API_KEY" ]]` block: POST with `model: gpt-4o-mini` and one user message; assert HTTP 200

**Checkpoint**: `make smoke` shows `[PASS] POST /v1/chat/completions — authenticated`; `pytest tests/contract/test_chat_completions.py::test_basic_chat_completion` passes.

---

## Phase 4: User Story 2 — Traceability via Phoenix Arize (P1)

**Goal**: Every completed chat request produces a span in Phoenix carrying `llm.model_name`, `llm.token_count.prompt`, `llm.token_count.completion`, and optional `metadata.team` / `metadata.request_id` tags.

**Independent Test**: After a successful chat completion with `metadata.team` and `metadata.request_id`, query Phoenix REST API and verify the span exists with the three required OpenInference attributes.

### Contract Test for US2

- [x] T012 [US2] Add `test_phoenix_span_attributes` to `tests/contract/test_chat_completions.py`: POST a request with `metadata.team = "test-team"` and `metadata.request_id = "req-test-001"`; after 200, query `GET http://localhost:6006/v1/spans?limit=1`; assert `llm.model_name` equals the requested model; assert `llm.token_count.prompt` and `llm.token_count.completion` match `usage` values in the API response; skip test with message if `PHOENIX_BASE_URL` env var not set (obs profile only)
- [x] T013 [P] [US2] Add `test_phoenix_metadata_tags` to `tests/contract/test_chat_completions.py`: same request with team/request_id; verify both appear as attributes on the span in Phoenix; skip if `PHOENIX_BASE_URL` not set

### Implementation for US2

- [x] T014 [US2] Add `PHOENIX_BASE_URL` to `tests/contract/conftest.py` as an optional session-scoped fixture (default `http://localhost:6006`); mark Phoenix-dependent tests to skip when the URL is unreachable

**Checkpoint**: With obs profile running, `pytest tests/contract/test_chat_completions.py -k phoenix` passes; Phoenix UI at `http://localhost:6006` shows span with correct attributes.

---

## Phase 5: User Story 3 — Prompt-Linked Langfuse Trace (P1)

**Goal**: Every completed request produces a Langfuse trace; when `metadata.prompt_name` is provided, the trace links to the `production` version of that prompt.

**Independent Test**: Send a request with `metadata.prompt_name: "system-v1"` (after registering the prompt); query Langfuse API and verify the trace exists and references a prompt version. Also verify an unlinked trace is created when `prompt_name` is absent.

### Contract Test for US3

- [x] T015 [US3] Add `test_langfuse_trace_created` to `tests/contract/test_chat_completions.py`: POST a request without `prompt_name`; wait 1s for async processing; query `GET http://localhost:3002/api/public/traces?limit=1` with Basic auth (`LANGFUSE_PUBLIC_KEY:LANGFUSE_SECRET_KEY`); assert a trace exists; assert `metadata.team` and `metadata.request_id` are present if sent; skip if `LANGFUSE_BASE_URL` not set
- [x] T016 [P] [US3] Add `test_langfuse_prompt_linked_trace` to `tests/contract/test_chat_completions.py`: POST a request with `metadata.prompt_name: "system-v1"`; query Langfuse trace; assert `promptName` field equals `"system-v1"`; skip if `LANGFUSE_BASE_URL` not set; note in docstring that this test requires a production-labelled prompt named `system-v1` to exist in Langfuse
- [x] T017 [P] [US3] Add `test_langfuse_metadata_tags` to `tests/contract/test_chat_completions.py`: POST with `team` and `request_id`; verify both appear in Langfuse trace metadata; skip if `LANGFUSE_BASE_URL` not set

### Implementation for US3

- [x] T018 [US3] Add `LANGFUSE_BASE_URL` and `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` as optional session-scoped fixtures to `tests/contract/conftest.py` (read from env, skip Langfuse tests if not set); add `langfuse_auth_headers` fixture returning Basic auth header

**Checkpoint**: With obs profile running, `pytest tests/contract/test_chat_completions.py -k langfuse` passes; Langfuse UI at `http://localhost:3002` shows trace with model and optional prompt link.

---

## Phase 6: User Story 4 — Reject Invalid Model Names (P1)

**Goal**: Unknown model names return HTTP 400 with a structured error body in under 50 ms (no upstream call). Empty `messages` array returns HTTP 400.

**Independent Test**: POST with `model: "nonexistent-model-xyz"` returns HTTP 400 with `error.message` naming the invalid model; response arrives in < 50 ms.

### Contract Test for US4

- [x] T019 [US4] Add `test_invalid_model_returns_400` to `tests/contract/test_chat_completions.py`: POST with `model: "nonexistent-model-xyz"` and one message; assert HTTP 400; assert response body contains an `error` key; assert `"nonexistent-model-xyz"` appears somewhere in the error `message` string; assert elapsed time < 0.5 s (generous bound — well within p99 < 50 ms spec)
- [x] T020 [P] [US4] Add `test_empty_messages_returns_400` to `tests/contract/test_chat_completions.py`: POST with a valid model and `messages: []`; assert HTTP 400
- [x] T021 [P] [US4] Add `test_unauthenticated_chat_returns_401` to `tests/contract/test_chat_completions.py`: POST without `Authorization` header; assert HTTP 401

**Checkpoint**: `pytest tests/contract/test_chat_completions.py -k "400 or 401"` passes with no obs profile required.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Smoke test integration, env hygiene, graceful degradation verification.

- [ ] T022 Run `make smoke` against core profile and verify `[PASS]` for new POST probe; fix any failures before marking done
- [x] T023 [P] Add `make up-obs` and `make down-obs` targets (or verify they already work) in `Makefile` if not present; the `obs` profile should map to `docker compose --profile obs up -d`
- [ ] T024 [P] Run `pytest tests/contract/test_chat_completions.py` full suite against running core + obs stack; confirm all tests pass or skip correctly (Phoenix/Langfuse skips are expected on core-only)
- [ ] T025 Validate quickstart.md Scenario 1 (basic completion) and Scenario 6 (invalid model 400) manually against the running stack

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — verify env and init-db.sql before anything else
- **Phase 2 (Foundational)**: Depends on Phase 1 — blocks US2 and US3 obs verification
- **Phase 3 (US1)**: Can start after Phase 2; US1 contract test and smoke probe work on core profile
- **Phase 4 (US2)**: Depends on Phase 2 (Phoenix running) and Phase 3 (conftest fixtures)
- **Phase 5 (US3)**: Depends on Phase 2 (Langfuse running) and Phase 3 (conftest fixtures); can run in parallel with Phase 4
- **Phase 6 (US4)**: Depends only on Phase 3 conftest fixtures; can run after Phase 3 or in parallel with Phase 4/5
- **Phase 7 (Polish)**: Depends on all story phases

### User Story Dependencies

- **US1 (P1)**: No story dependencies — start after foundational
- **US2 (P1)**: Needs Phoenix running (Phase 2); independent of US1 content but shares conftest
- **US3 (P1)**: Needs Langfuse running (Phase 2); independent of US1/US2 content but shares conftest
- **US4 (P1)**: No obs dependency — pure LiteLLM validation; shares conftest with US1

### Within Each User Story

- Add fixtures to conftest.py before writing tests that need them
- Contract tests before smoke test extensions
- docker-compose.yml changes before any obs-profile test run

### Parallel Opportunities

- T005 (langfuse-worker) and T006 (volumes) can run in parallel with T004 (langfuse-server)
- T012 (phoenix span test) and T013 (phoenix metadata test) can be written in parallel
- T015, T016, T017 (Langfuse tests) can be written in parallel once T018 (conftest) is done
- T019, T020, T021 (US4 tests) can all be written in parallel
- T022 (smoke), T023 (Makefile), T024 (pytest full run) can be done in parallel in Phase 7

---

## Parallel Example: Phase 2 (Foundational Docker Compose)

```bash
# These touch different service blocks in docker-compose.yml — do sequentially
# (same file), but plan the content in parallel:

Task T003: arize-phoenix service block
Task T004: langfuse-server service block
Task T005: langfuse-worker service block  ← after T004 (depends_on langfuse-server)
Task T006: volumes block                  ← can be appended independently
Task T007: litellm env verification       ← independent read/verify
```

## Parallel Example: Phase 6 (US4 contract tests — same file, write together)

```bash
Task T019: test_invalid_model_returns_400
Task T020: test_empty_messages_returns_400   ← same file, but distinct test functions
Task T021: test_unauthenticated_chat_returns_401
```

---

## Implementation Strategy

### MVP First (US1 — Core Inference Path)

1. Complete Phase 1: env check + init-db.sql
2. Complete Phase 2: obs-profile services in docker-compose.yml
3. Complete Phase 3: US1 contract test + smoke test probe
4. **STOP and VALIDATE**: `make smoke` passes; basic chat completion test passes on core profile
5. Confirm the platform returns valid completions before adding observability verification

### Incremental Delivery

1. Setup + Foundational → `make up-obs` healthy
2. US1 → smoke test passes → MVP: inference path confirmed
3. US2 → Phoenix span tests pass → Observability confirmed
4. US3 → Langfuse trace tests pass → Prompt governance confirmed
5. US4 → 400/401 tests pass → Error path confirmed
6. Polish → full contract suite green

---

## Notes

- All contract tests share `tests/contract/test_chat_completions.py` — write sequentially to avoid merge conflicts
- Phoenix and Langfuse tests are marked skip (not fail) when their env vars are absent; this lets the core-profile CI pass without obs-profile
- No custom instrumentation code is added anywhere — all observability is callback-driven via LiteLLM's `arize_phoenix` and `langfuse` callbacks
- Prompt content is never asserted in tests — only token counts, model names, finish_reason, and metadata tags
- `[P]` on docker-compose tasks means the service blocks can be authored in parallel but must be merged into the single file carefully to avoid YAML conflicts
