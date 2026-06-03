---
description: "Task list for 015-gateway-body-size-limit"
---

# Tasks: Gateway Request Body Size Limit

**Input**: Design documents from `specs/015-gateway-body-size-limit/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/ ✅ | quickstart.md ✅

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on concurrent tasks)
- **[Story]**: User story this task belongs to (US1, US2, US3)
- Exact file paths in all descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the Kong plugin is available and the seeding pattern is understood before any changes are made.

- [x] T001 Verify `request-size-limiting` plugin is available in Kong 3.6 by checking `GET http://localhost:8001/` — confirm `request-size-limiting` appears in `plugins.available_on_server`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Configuration changes that all three user stories depend on. Both files must be updated together to keep DB-mode (seed script) and DB-less mode (declarative YAML) in sync.

**⚠️ CRITICAL**: All user story smoke tests require T002 and T003 to be complete and `make seed-kong` to have been re-run.

- [x] T002 Add `create_request_size_plugin()` function to `scripts/seed-kong.sh` — idempotent, uses `_plugin_exists_global "request-size-limiting"` guard, calls `POST /plugins` with `name=request-size-limiting`, `config.allowed_payload_size=10`; add call to `create_request_size_plugin` inside `main()` after `create_rate_limiting_plugin`
- [x] T003 [P] Add `request-size-limiting` entry to the `plugins:` block in `services/kong/kong.yml` with `config.allowed_payload_size: 10` — mirrors T002 for DB-less mode; place after the `response-transformer` plugin entry

**Checkpoint**: After `make seed-kong`, `GET http://localhost:8001/plugins?name=request-size-limiting` must return a non-empty `data` array with `config.allowed_payload_size = 10`.

---

## Phase 3: User Story 1 — Oversized Payload Rejected at Gateway (Priority: P1) 🎯 MVP

**Goal**: Any inference request body > 10 MB is rejected at Kong with HTTP 413 before reaching any upstream service.

**Independent Test**: Send a >10 MB POST to `/v1/chat/completions` with a valid API key; expect exactly `413`. Confirm no traffic reaches LiteLLM by checking its logs.

### Implementation for User Story 1

- [x] T004 [US1] Add oversized-payload smoke test block to `scripts/smoke-test.sh` — generate an 11 MB payload inline using `python3 -c "..."`, POST to `http://localhost:8080/v1/chat/completions` with `$SMOKE_API_KEY`, assert HTTP status is `413`; label block `# [015] Request size limit — oversized payload (expect 413)`
- [x] T005 [US1] Add boundary smoke test to `scripts/smoke-test.sh` — generate a payload at exactly `allowed_payload_size` (10 MB content string), POST to `/v1/chat/completions`, assert response is NOT `413` (accept `200`, `400`, or `422`); label block `# [015] Request size limit — boundary at 10 MB (expect pass-through)`

**Checkpoint**: `make smoke` passes. `make logs svc=litellm` shows zero entries for the oversized request. The boundary request appears in LiteLLM logs.

---

## Phase 4: User Story 2 — Requests Within Limit Pass Through Unaffected (Priority: P2)

**Goal**: Confirm legitimate traffic (body ≤ 10 MB) is not disrupted by the size enforcement layer.

**Independent Test**: Send a standard chat completion request (~1 KB); expect `200`. Verify no regression in health check and model catalogue routes.

### Implementation for User Story 2

- [x] T006 [US2] Review existing smoke test cases in `scripts/smoke-test.sh` — confirm the standard chat completion test (`/v1/chat/completions` with small body), health check (`/health`), and model catalogue (`/v1/models`) tests are present and will still pass after T002/T003 changes; add a comment `# Unaffected by [015] size limit` above each if not already annotated
- [x] T007 [US2] Run `make smoke` end-to-end and confirm all pre-existing test cases still return their expected status codes — document any failure as a regression; no code change should be needed if T002/T003 are correct

**Checkpoint**: `make smoke` exits 0. All pre-existing assertions pass without modification.

---

## Phase 5: User Story 3 — Consistent Enforcement Across All Inference Routes (Priority: P3)

**Goal**: Verify that the global plugin scope means every inference route type (embeddings, streaming, batch if seeded) also enforces the 10 MB limit — not just `/v1/chat/completions`.

**Independent Test**: Send an oversized POST to `/v1/embeddings`; expect `413`. One test per route type is sufficient.

### Implementation for User Story 3

- [x] T008 [US3] Add embeddings route oversized-payload test to `scripts/smoke-test.sh` — generate an 11 MB payload, POST to `http://localhost:8080/v1/embeddings`, assert `413`; label block `# [015] Request size limit — embeddings route (expect 413)`
- [x] T009 [P] [US3] Add response-header assertion to `scripts/smoke-test.sh` for the 413 case — after the oversized POST, capture response headers with `curl -D -`, assert `X-Request-ID` header is present and non-empty, assert `X-Platform: inference-platform` header is present; label block `# [015] Request size limit — 413 carries correlation headers`

**Checkpoint**: `make smoke` passes all three story phases. Every inference route tested returns `413` for oversized payloads.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Observability verification, documentation, and environment variable configurability.

- [x] T010 Verify Kong access log captures 413 rejections in Loki — with `obs` profile running, query Loki for `{service="kong"} | json | status="413"` and confirm entries include `request_id`, `path`, `method`, and `status` fields; add query to `quickstart.md` under "Acceptance Tests → Test 6" if not already present
- [x] T011 [P] Confirm `allowed_payload_size` is expressed as an environment-variable-driven value in `scripts/seed-kong.sh` — introduce `REQUEST_SIZE_LIMIT_MB=${REQUEST_SIZE_LIMIT_MB:-10}` at the top of the file and use `$REQUEST_SIZE_LIMIT_MB` in the `create_request_size_plugin` curl call; update `.env.example` with `REQUEST_SIZE_LIMIT_MB=` (no value)
- [x] T012 [P] Update `docs/progress.md` — mark feature 015 as complete, set next active feature to the following planned feature

---

## Dependencies (Story Completion Order)

```
T001 (verify plugin available)
  └─→ T002, T003 [can run in parallel] (seed script + declarative YAML)
        └─→ make seed-kong (manual step)
              ├─→ T004, T005 (US1 — chat completions oversized + boundary)
              ├─→ T006, T007 (US2 — pass-through regression check)
              └─→ T008, T009 (US3 — embeddings route + header assertion)
                    └─→ T010, T011, T012 [can run in parallel] (polish)
```

US2 and US3 can begin immediately after `make seed-kong` completes — they do not depend on each other.

---

## Parallel Execution Opportunities

| Parallel Group | Tasks | Why safe |
|---|---|---|
| Foundation | T002, T003 | Different files (`seed-kong.sh` vs `kong.yml`) |
| US1 tests | T004, T005 | Different smoke test blocks, same file — write in sequence |
| US3 tests | T008, T009 | T009 reads output of T008's curl — write T008 first, T009 can reuse the same curl invocation |
| Polish | T010, T011, T012 | Loki query (read-only), env var change, docs update — all independent |

---

## Implementation Strategy

**MVP** = Phase 2 + Phase 3 (T001–T005): installs the plugin and proves the core rejection behaviour works.

**Full feature** = all phases through T012: all routes verified, pass-through regression confirmed, observability checked, configurability documented.

Since this feature is pure Kong configuration with no new services, the entire implementation can typically be completed in a single sitting. Start with `make seed-kong` after T002/T003, then run `make smoke` after each story phase.
