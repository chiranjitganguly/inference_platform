# Tasks: Model Catalogue API

**Input**: Design documents from `specs/004-model-catalogue-api/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/openapi.yaml ✅ | quickstart.md ✅

**Tests**: Contract test included — SC-004 in spec.md explicitly requires schema validation via contract test.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)

---

## Phase 1: Setup (Service Scaffolding)

**Purpose**: Create the file and Docker Compose structure that all user story phases depend on. No user story work can begin until `docker-compose.yml` has both services defined.

- [x] T001 [P] Create `services/litellm/config.yaml` with top-level YAML skeleton: `model_list: []`, `litellm_settings: {}`, `general_settings: {}`
- [x] T002 [P] Create `services/kong/kong.yml` with declarative skeleton: `_format_version: "3.0"`, empty `services:` list
- [x] T003 [P] Create `tests/contract/__init__.py` (empty) and `tests/contract/conftest.py` with `KONG_BASE_URL` and `SMOKE_API_KEY` fixtures sourced from environment variables
- [x] T004 Add `litellm` service block to `docker-compose.yml` under the `core` profile: image `ghcr.io/berriai/litellm:main-v1.52.0`, volume mount `./services/litellm/config.yaml:/app/config.yaml:ro`, no host binding for port 4000 (internal only), `depends_on: postgres`
- [x] T005 Add `kong` service block to `docker-compose.yml` under the `core` profile: image `kong:3.6`, port `8080:8080` (host-bound, the only public port), `KONG_DATABASE: "off"`, declarative config mount `./services/kong/kong.yml:/etc/kong/declarative/kong.yml:ro`, `depends_on: litellm`, healthcheck on Kong Admin `/status`

**Checkpoint**: `make ps` shows postgres, litellm, kong all present. `make up-core` starts all three.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: LiteLLM model list, Kong routing, and key-auth enforcement. **Must complete before any user story begins** — without auth and routing in place, all story acceptance scenarios fail.

⚠️ **CRITICAL**: No user story work can begin until this phase is complete.

- [x] T006 Complete `services/litellm/config.yaml` `litellm_settings` block: `master_key: os.environ/LITELLM_MASTER_KEY`, `callbacks: [arize_phoenix, langfuse, prometheus]`, `success_callback`, `failure_callback` — all three callbacks required per constitution
- [x] T007 Add all 11 model entries to `services/litellm/config.yaml` `model_list` with `model_name` and `litellm_params` only (no `model_info` yet): `gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `o4-mini` (provider `openai/`), `claude-sonnet`, `claude-haiku` (provider `anthropic/`), `gemini-pro`, `gemini-flash` (provider `gemini/`), `command-r-plus` (provider `cohere/`), `text-embedding-3-small`, `text-embedding-3-large` (provider `openai/`)
- [x] T008 Complete `services/kong/kong.yml` with litellm upstream service (`url: http://litellm:4000`), route for `GET /v1/models` (name: `models-catalogue`, methods: `[GET]`, paths: `[/v1/models]`), and `key-auth` plugin on that route (`key_names: [Authorization]`, `key_in_header: true`, `hide_credentials: true`)
- [x] T009 Update `scripts/seed-kong.sh` to create a `smoke-test-consumer` Kong consumer and provision one API key for it, printing the key so it can be exported as `SMOKE_API_KEY`

**Checkpoint**: `make up-core && make seed-kong` succeeds. `curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/v1/models` returns `401`. `curl -s http://localhost:8080/v1/models -H "Authorization: Bearer $SMOKE_API_KEY"` returns `200` with `{"object":"list","data":[]}` (empty until Phase 3).

---

## Phase 3: User Story 1 — Browse Full Model Catalogue (Priority: P1) 🎯 MVP

**Goal**: Authenticated developers can call `GET /v1/models` and receive all 11 platform models, each with complete metadata, from exactly four providers.

**Independent Test**: `curl -s http://localhost:8080/v1/models -H "Authorization: Bearer $SMOKE_API_KEY" | jq '.data | length'` outputs `11`.

### Contract Test for User Story 1

- [x] T010 [US1] Write `tests/contract/test_models_catalogue.py` initial assertions: HTTP 200 with valid key, response `Content-Type: application/json`, envelope `object == "list"`, `data` is a list, `len(data) == 11`, providers set equals `{"openai", "anthropic", "google", "cohere"}`, every entry has `id`, `owned_by`, `model_info` with `provider`, `tier`, `type`, `status`, `context_window`, `capabilities`

### Implementation for User Story 1

- [x] T011 [US1] Add `model_info` block to each of the 9 chat models in `services/litellm/config.yaml` with `provider`, `tier` (`standard`/`premium`), `type: chat`, `status: available`, `context_window` (see data-model.md for all values), `capabilities` list — values from the reference table in `research.md`
- [x] T012 [US1] Update `scripts/smoke-test.sh`: replace the existing unauthenticated `/v1/models` probe with an authenticated probe (`-H "Authorization: Bearer ${SMOKE_API_KEY:-}"`) and assert HTTP 200

**Checkpoint**: `SMOKE_API_KEY=$API_KEY pytest tests/contract/test_models_catalogue.py -v` — all T010 assertions pass. `SMOKE_API_KEY=$API_KEY make smoke` — `/v1/models` probe shows `[PASS]`.

---

## Phase 4: User Story 2 — Distinguish Chat vs Embedding Models (Priority: P1)

**Goal**: The `type` field (`chat`|`embedding`) is present on every model entry. Embedding models additionally carry `dimensions`. Filtering by type programmatically yields exactly 9 chat and 2 embedding models.

**Independent Test**: `curl ... /v1/models | jq '[.data[] | select(.model_info.type == "embedding")] | length'` outputs `2`. Each embedding entry has a `dimensions` integer field; no chat entry has `dimensions`.

### Contract Test for User Story 2

- [x] T013 [US2] Extend `tests/contract/test_models_catalogue.py`: assert exactly 9 entries have `type == "chat"` and no `dimensions` key in `model_info`, assert exactly 2 entries have `type == "embedding"` and a positive integer `dimensions` key in `model_info`, assert embedding entries have `capabilities == ["embeddings"]`

### Implementation for User Story 2

- [x] T014 [US2] Add `model_info` block to `text-embedding-3-small` in `services/litellm/config.yaml`: `provider: openai`, `tier: standard`, `type: embedding`, `status: available`, `context_window: 8191`, `capabilities: [embeddings]`, `dimensions: 1536`
- [x] T015 [US2] Add `model_info` block to `text-embedding-3-large` in `services/litellm/config.yaml`: `provider: openai`, `tier: premium`, `type: embedding`, `status: available`, `context_window: 8191`, `capabilities: [embeddings]`, `dimensions: 3072`

**Checkpoint**: `pytest tests/contract/test_models_catalogue.py -v` — all T013 assertions pass alongside T010 assertions. `jq '[.data[] | select(.model_info.type=="embedding")] | length'` returns `2`.

---

## Phase 5: User Story 3 — Reject Unauthenticated Requests (Priority: P1)

**Goal**: Requests with no key, an invalid key, or a malformed Authorization header all receive HTTP 401. The catalogue data is never returned to unauthenticated callers.

**Independent Test**: Three curl probes — no header, bad key, wrong scheme — all return `401`.

### Contract Test for User Story 3

- [x] T016 [US3] Extend `tests/contract/test_models_catalogue.py`: assert GET `/v1/models` with no `Authorization` header returns HTTP 401, assert GET `/v1/models` with `Authorization: Bearer invalid-key-xyz` returns HTTP 401, assert GET `/v1/models` with `Authorization: Basic dXNlcjpwYXNz` returns HTTP 401

### Implementation for User Story 3

- [x] T017 [US3] Add a second smoke probe to `scripts/smoke-test.sh` for the negative auth case: `probe "LiteLLM /v1/models — unauthenticated" "${KONG}/v1/models" 401` (no auth header — expects 401)

**Checkpoint**: `pytest tests/contract/test_models_catalogue.py -v` — all T016 assertions pass. `make smoke` shows both `[PASS]    LiteLLM /v1/models via Kong — authenticated` and `[PASS]    LiteLLM /v1/models — unauthenticated (HTTP 401)`.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Quality gates, memory budget, and end-to-end quickstart validation.

- [x] T018 [P] Run `ruff check tests/contract/` and `mypy tests/contract/ --ignore-missing-imports` — fix any warnings or type errors to meet constitution §10.1 code quality gates
- [x] T019 [P] Verify `.env.example` lists all new required variables: `LITELLM_MASTER_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `COHERE_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `OTEL_EXPORTER_OTLP_ENDPOINT` — add any missing names (values must remain empty)
- [ ] T020 Run `make stats` after `make up-core` and confirm total memory across postgres + litellm + kong stays under 620 MB (constitution §7.4 core profile budget)
- [ ] T021 Execute the quickstart.md validation sequence end-to-end: `make up-core`, `make seed-kong`, three curl probes from quickstart.md steps 3–4, `SMOKE_API_KEY=$API_KEY make smoke`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — T001, T002, T003 can all start in parallel; T004 and T005 depend on T001/T002 respectively
- **Foundational (Phase 2)**: Depends on Phase 1 completion — **BLOCKS all user stories**
- **User Story phases (3–5)**: All depend on Phase 2 completion; US1, US2, US3 can run in priority order (all are P1) but US2 implicitly completes US1's config work
- **Polish (Phase 6)**: Depends on all user story phases complete

### User Story Dependencies

- **US1 (Phase 3)**: Requires Phase 2 ✅ — no dependency on US2 or US3
- **US2 (Phase 4)**: Requires Phase 2 ✅ — no dependency on US3; extends config.yaml and contract test started in US1
- **US3 (Phase 5)**: Requires Phase 2 ✅ (key-auth plugin already wired in T008) — no dependency on US1 or US2 beyond the route existing

### Within Each Phase

- Contract test (where present) written before or alongside implementation
- Config.yaml changes require LiteLLM restart (`make restart svc=litellm`) to take effect
- Kong config is declarative and loaded at startup — changes require `make restart svc=kong`

### Parallel Opportunities

- T001, T002, T003 all target different files — run in parallel
- T004 and T005 both edit `docker-compose.yml` — run sequentially
- T006, T007, T008, T009 all target different files — run in parallel (T006/T007 share config.yaml but are separate blocks, edit sequentially)
- T010 (contract test) and T011 (config.yaml) are in different files — write in parallel, run contract test after LiteLLM restarts with new config
- T014 and T015 target the same file (config.yaml) — run sequentially

---

## Parallel Example: Phase 1 Setup

```
Parallel batch 1 (all different files):
  T001 — services/litellm/config.yaml skeleton
  T002 — services/kong/kong.yml skeleton
  T003 — tests/contract/__init__.py + conftest.py

Sequential after batch 1:
  T004 — docker-compose.yml litellm block
  T005 — docker-compose.yml kong block
```

## Parallel Example: User Story 1

```
After Phase 2 complete:
  Parallel:
    T010 — Write contract test assertions
    T011 — Add chat model model_info to config.yaml
  Sequential after both:
    T012 — Update smoke-test.sh probe
    restart litellm + run pytest
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup — file scaffolding + docker-compose
2. Complete Phase 2: Foundational — litellm config + kong routing + key-auth
3. Complete Phase 3: US1 — chat model metadata + contract test + smoke test
4. **STOP and VALIDATE**: `make smoke` and `pytest tests/contract/` both pass
5. MVP deliverable: authenticated GET /v1/models returns 9 chat models with metadata

### Incremental Delivery

1. Setup + Foundational → `make up-core && make seed-kong` → 401 gate working
2. US1 → 9 chat models with metadata → MVP
3. US2 → 2 embedding models with dimensions → type distinction working
4. US3 → contract test covers all 3 auth failure cases + negative smoke probe
5. Polish → quality gates + memory budget + quickstart validated

### Single-Developer Sequence (Recommended Order)

```
T001 → T002 → T003 → T004 → T005 →   (Phase 1)
T006 → T007 → T008 → T009 →           (Phase 2 — restart services after)
T010 → T011 → T012 →                  (US1 — restart litellm after T011)
T013 → T014 → T015 →                  (US2 — restart litellm after T015)
T016 → T017 →                         (US3)
T018 → T019 → T020 → T021             (Polish)
```

---

## Notes

- `[P]` tasks edit different files and have no shared dependencies — safe to run in parallel
- LiteLLM reads config.yaml only at startup — run `make restart svc=litellm` after every config.yaml change
- Kong reads declarative config at startup — run `make restart svc=kong` after every kong.yml change
- Commit after each checkpoint to keep progress recoverable
- `SMOKE_API_KEY` must be exported from `make seed-kong` output before running smoke tests or contract tests
