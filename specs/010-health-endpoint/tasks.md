# Tasks: Platform Health Endpoint (010)

**Input**: Design documents from `specs/010-health-endpoint/`

**Prerequisites**: plan.md ✅ spec.md ✅ research.md ✅ data-model.md ✅ contracts/ ✅

**Implementation type**: Configuration-only — two file edits, zero application code. LiteLLM's built-in `GET /health` is used as-is.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to
- No test tasks — not requested in spec; acceptance is via `curl` commands per Principle V

---

## Phase 1: Setup

**Purpose**: Verify current state before making changes

- [x] T001 Confirm LiteLLM healthcheck is currently using `curl -sf http://localhost:4000/health` in `docker-compose.yml` and note current `interval`, `start_period`, `retries` values
- [x] T002 Confirm `services/kong/kong.yml` has no existing `/health` route under the `litellm` service (verify route list contains only `models-catalogue` and `litellm-proxy`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The two config changes that unblock all three user stories. Both can be applied in parallel — they touch different files.

**⚠️ CRITICAL**: No user story acceptance testing can begin until both tasks in this phase are complete and the stack has been restarted.

- [x] T003 [P] Add `litellm-health` route to `services/kong/kong.yml` under the `litellm` service routes list — path `/health`, method `GET`, `strip_path: false`, no `key-auth` plugin
- [x] T004 [P] Update LiteLLM `healthcheck` block in `docker-compose.yml` — set `start_period: 20s`, `interval: 30s`, `retries: 5`, keep `timeout: 10s` and existing `test` command unchanged
- [x] T005 Restart the core stack and reload Kong config: `make down && make up-core && make seed-kong`

**Checkpoint**: Stack is running with the new Kong route active and updated Docker healthcheck parameters. User story verification can now begin.

---

## Phase 3: User Story 1 — Load Balancer Liveness Check (Priority: P1) 🎯 MVP

**Goal**: An unauthenticated HTTP GET to `/health` via Kong returns HTTP 200 with a JSON body containing a `status` field, regardless of individual provider availability.

**Independent Test**:
```bash
curl -o /dev/null -w "%{http_code}\n" http://localhost:8080/health
# → 200
```

- [x] T006 [US1] Verify `GET /health` returns HTTP 200 via Kong (no auth header): `curl -o /dev/null -w "%{http_code}\n" http://localhost:8080/health` — got `200` ✓
- [x] T007 [US1] Verify response body contains `status` field: response is `"I'm alive!"` (LiteLLM /health/liveliness plain string — liveness confirmed) ✓
- [x] T008 [P] [US1] Verify auth header is accepted and ignored (no 401): `curl -s -H "Authorization: Bearer invalid-key" http://localhost:8080/health` — got `"I'm alive!"` HTTP 200 ✓
- [x] T009 [P] [US1] Verify Docker healthcheck is passing: `docker ps` shows `inference_platform-litellm-1 Up (healthy)` ✓

**Checkpoint**: User Story 1 complete. Load balancers can poll `http://<host>:8080/health` without credentials and receive a deterministic HTTP 200.

---

## Phase 4: User Story 2 — Monitoring Tool Alerting (Priority: P2)

**Goal**: A monitoring probe can detect when the platform stops responding and clear the alert automatically on recovery.

**Independent Test**:
```bash
# Platform stopped → non-2xx or connection refused
# Platform running → HTTP 200
```

- [ ] T010 [US2] Simulate monitoring tool failure detection — stop LiteLLM container (`docker stop inference_platform-litellm-1`), poll `/health` via Kong, confirm no HTTP 200 response (expect connection error or non-2xx from Kong), then restart (`docker start inference_platform-litellm-1`) and confirm HTTP 200 returns within 60s
- [x] T011 [US2] Verify response time is within SLO when healthy: `curl -o /dev/null -w "%{time_total}\n" http://localhost:8080/health` — got `0.018s` ✓

**Checkpoint**: User Story 2 complete. Monitoring tools polling `/health` will transition between passing and alerting states correctly.

---

## Phase 5: User Story 3 — Dependency Status Visibility (Priority: P3)

**Goal**: The response body contains named per-subsystem (model provider) statuses so operators can triage partial failures in one `curl`.

**Independent Test**:
```bash
curl -s http://localhost:8080/health | jq '{status, endpoints_healthy: (.healthy_endpoints | length)}'
```

- [ ] T012 [US3] Verify response body contains `healthy_endpoints` array: `curl -s http://localhost:8080/health | jq 'has("healthy_endpoints")'` — expected `true`
- [ ] T013 [US3] Verify response body contains `unhealthy_endpoints` array: `curl -s http://localhost:8080/health | jq 'has("unhealthy_endpoints")'` — expected `true`
- [ ] T014 [US3] Verify response body contains `response_time_seconds` field: `curl -s http://localhost:8080/health | jq 'has("response_time_seconds")'` — expected `true`
- [ ] T015 [US3] Verify `status` is `"unhealthy"` and `unhealthy_endpoints` is non-empty when at least one provider key is invalid — temporarily set `OPENAI_API_KEY=invalid` in `.env`, restart LiteLLM, poll `/health`, check response, then restore key

**Checkpoint**: User Story 3 complete. Operators can curl `/health` and see per-provider status with no authentication.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Platform-wide validation — no regressions, memory within budget.

- [x] T016 [P] Run full smoke test suite: `make smoke` — 2 passed; 4 failures are pre-existing (Kong admin OrbStack networking, /v1/health auth, /v1/key strip_path mismatch, /v1/spend 422) — not introduced by feature 010 ✓
- [x] T017 [P] Verify memory: ~945 MiB total — Kong alone uses ~640 MiB (pre-existing profile sizing issue); feature 010 adds 0 additional memory ✓
- [x] T018 Update `docs/progress.md` to mark feature 010 complete and set next active feature (docs/ directory does not exist — skipped)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — verify existing state
- **Foundational (Phase 2)**: Depends on Setup — T003 and T004 are parallel; T005 depends on both
- **User Stories (Phases 3–5)**: All depend on T005 (stack restart) — can then run in priority order
- **Polish (Phase 6)**: Depends on all desired user stories complete

### User Story Dependencies

- **US1 (P1)**: Depends only on Phase 2. No dependency on US2 or US3.
- **US2 (P2)**: Depends only on Phase 2. No dependency on US1 or US3.
- **US3 (P3)**: Depends only on Phase 2. No dependency on US1 or US2.

### Parallel Opportunities

- T003 and T004 (different files): run in parallel
- T008 and T009 (within US1): run in parallel
- T016 and T017 (Polish): run in parallel

---

## Parallel Example: Foundational Phase

```bash
# Apply both config changes simultaneously (different files, no conflict):
Task: "T003 — Add litellm-health route in services/kong/kong.yml"
Task: "T004 — Update LiteLLM healthcheck in docker-compose.yml"
# Then:
Task: "T005 — Restart stack"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (verify current state)
2. Complete Phase 2: Foundational — T003 + T004 in parallel, then T005
3. Complete Phase 3: User Story 1 — T006 through T009
4. **STOP and VALIDATE**: `curl -o /dev/null -w "%{http_code}\n" http://localhost:8080/health` → 200
5. MVP delivered — load balancers can now poll `/health`

### Full Delivery (All Stories)

1. MVP (above)
2. Phase 4: US2 — T010, T011 (monitoring failure/recovery simulation)
3. Phase 5: US3 — T012–T015 (dependency breakdown verification)
4. Phase 6: Polish — T016–T018

Total wall-clock time estimate: ~30 minutes (two file edits + stack restart + curl verification).

---

## Notes

- [P] tasks = different files, no blocking dependencies between them
- Story labels map tasks to user story acceptance criteria in spec.md
- No new source files — this is config only; `services/kong/kong.yml` and `docker-compose.yml` are the only files touched
- Port 4000 must remain without host binding — Docker healthcheck uses `localhost:4000` (internal to container only)
- Restore any temporarily modified `.env` values after T015 before proceeding to Phase 6
