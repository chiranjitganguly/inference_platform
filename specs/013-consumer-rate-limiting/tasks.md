# Tasks: Per-Consumer Gateway Rate Limiting

**Input**: Design documents from `specs/013-consumer-rate-limiting/`

**Branch**: `013-consumer-rate-limiting` | **Date**: 2026-05-31

**Prerequisites**: plan.md ✅ · spec.md ✅ · research.md ✅ · data-model.md ✅ · contracts/kong-rate-limiting-plugin.md ✅ · quickstart.md ✅

**Tests**: No test tasks generated — not requested in the feature specification. Acceptance is via curl smoke-test assertions per spec SC-001 through SC-007.

**Scope**: Infrastructure-only. All changes are additive to three existing files:
- `scripts/seed-kong.sh` — new plugin functions + calls in main seed sequence
- `scripts/smoke-test.sh` — five new test probe sections
- `services/prometheus/rules.yml` — one new alert rule group

No new services, containers, or directories.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no shared dependencies)
- **[Story]**: User story this task belongs to (US1–US4)

---

## Phase 1: Setup (Read Existing Files)

**Purpose**: Understand current patterns before making additive changes. Each seed function follows a strict idempotency convention; each smoke section follows a consistent exit-code convention. Read before writing.

- [x] T001 Read `scripts/seed-kong.sh` from top to bottom — note the `_plugin_exists_global` helper, `info`/`ok`/`err` logging functions, existing consumer names (especially `smoke-test-consumer`), and the order of function calls in `main()`
- [x] T002 [P] Read `services/prometheus/prometheus.yml` — identify whether a `job_name: kong` scrape job targeting `kong:8001` already exists
- [x] T003 [P] Read `services/prometheus/rules.yml` — identify the existing alert rule format (group name, alert name, expr, for, labels, annotations structure)

**Checkpoint**: Current patterns confirmed — idempotency helpers located, prometheus scrape config status known, rules.yml format understood.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Install the global Kong rate-limiting plugin and ensure Prometheus can scrape Kong metrics. All user story verification depends on this phase completing successfully.

**⚠️ CRITICAL**: No user story work can be verified until T004–T006 complete and `make seed-kong` succeeds.

- [x] T004 In `services/prometheus/prometheus.yml` — if a `job_name: kong` scrape job targeting `kong:8001` is absent, add it under `scrape_configs`; if already present, confirm the target is `kong:8001` (the Kong Admin API metrics endpoint required by SC-007)

- [x] T005 Add `create_rate_limiting_plugin()` function to `scripts/seed-kong.sh` immediately before the `main()` function — the function must: (1) guard with `_plugin_exists_global rate-limiting`, (2) POST to `${KONG_ADMIN}/plugins` with fields: `name=rate-limiting`, `config.second=10`, `config.minute=300`, `config.hour=10000`, `config.policy=redis`, `config.redis_host=redis-cache`, `config.redis_port=6379`, `config.limit_by=consumer`, `config.fault_tolerant=true`, `config.hide_client_headers=false`, (3) log success with `ok "Global plugin: rate-limiting (10/s, 300/min, 10000/hr — Redis, by consumer)"`

- [x] T006 [P] In `scripts/seed-kong.sh` — update the existing prometheus plugin installation block (inside `create_global_plugins()` or equivalent) to include `-d "config.per_consumer=true"` so per-consumer `kong_http_requests_total` labels are emitted; guard the POST with `_plugin_exists_global prometheus`; log success with `ok "Global plugin: prometheus (per-consumer metrics)"`

- [x] T007 Add a call to `create_rate_limiting_plugin` inside `main()` in `scripts/seed-kong.sh` — insert it after any existing `create_global_plugins` call and before the final status log so execution order is: consumers → services → global plugins → rate-limiting plugin

- [ ] T008 Run `make seed-kong` and verify the plugin is active: `curl -sf http://localhost:8001/plugins?name=rate-limiting | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['config'])"` — expected output shows `second=10, minute=300, hour=10000, policy=redis, limit_by=consumer`; run `make seed-kong` a second time to confirm idempotency (no errors on re-run)

**Checkpoint**: `make seed-kong` completes without errors; rate-limiting plugin confirmed active in Admin API; all existing smoke test probes still pass.

---

## Phase 3: User Story 1 — Throttled Consumer Receives Actionable Error (Priority: P1) 🎯 MVP

**Goal**: A consumer that exceeds its per-second, per-minute, or per-hour limit receives HTTP 429 with a `Retry-After` header before any downstream call is made. The gateway applies the default policy (10/s, 300/min, 10,000/hr) to all consumers automatically.

**Independent Test**: Send 12 rapid requests with a single authenticated key. Requests 1–10 return 200; requests 11–12 return 429 with a `Retry-After` header. A second key's request returns 200 without interference.

**Implements**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-007, FR-009, FR-010 | SC-001, SC-006

### Implementation for User Story 1

- [x] T009 [US1] Add a `probe_rate_limit_burst()` section to `scripts/smoke-test.sh` — use `for i in $(seq 1 12)` sending `GET /v1/models` with `Authorization: ${SMOKE_API_KEY}`; capture each HTTP status code with `-o /dev/null -w '%{http_code}'`; assert requests 1–10 return 200 and requests 11–12 return 429 using the existing `ok`/`fail` helper functions; print each result as `Request N → HTTP NNN`; skip with a `[SKIP]` notice if `SMOKE_API_KEY` is unset; call this probe from the main smoke-test flow

- [x] T010 [P] [US1] Add a `probe_retry_after_header()` section to `scripts/smoke-test.sh` — exhaust the per-second limit with 11 rapid requests, capturing the full response of the 11th with `curl -si`; assert the response headers contain a `retry-after:` line (case-insensitive grep) with a numeric value ≥ 1; fail with a descriptive message if the header is absent; call this probe from the main smoke-test flow

**Checkpoint**: `make smoke` shows both new US1 probes passing. The 429 + Retry-After guarantee is live and independently verifiable. US1 is complete.

---

## Phase 4: User Story 2 — Consumer Isolation Under Saturation (Priority: P1)

**Goal**: Consumer A exhausting its quota does not affect Consumer B's success rate or latency.

**Independent Test**: Hammer 20 requests with Consumer A in a background subshell; immediately send 1 request with Consumer B; assert Consumer B returns 200.

**Implements**: FR-001 (per-identity counter isolation) | SC-002, SC-005

### Implementation for User Story 2

- [x] T011 [US2] Add `create_consumer_b()` function to `scripts/seed-kong.sh` — creates consumer `consumer-b` via `curl -sf -X PUT ${KONG_ADMIN}/consumers/consumer-b -d username=consumer-b`, then provisions a key credential via `POST ${KONG_ADMIN}/consumers/consumer-b/key-auth -d "key=${CONSUMER_B_API_KEY:-consumer-b-test-key}"`; suppress already-exists errors with `2>/dev/null || true`; log with `ok "Consumer: consumer-b provisioned"`; add a call to `create_consumer_b` inside `main()` immediately after the existing consumer creation calls

- [x] T012 [US2] Add a `probe_consumer_isolation()` section to `scripts/smoke-test.sh` — (1) fire 20 rapid background requests to `GET /v1/models` using `SMOKE_API_KEY` in a subshell, (2) sleep 50 ms, (3) send one request using `consumer-b-test-key` as the Authorization header and capture the HTTP status, (4) assert the status is 200 using `ok`/`fail` helpers, (5) wait for the background subshell to exit; call this probe from the main smoke-test flow

**Checkpoint**: `make smoke` shows the isolation probe passing. US2 is complete and independently verifiable.

---

## Phase 5: User Story 3 — Operator Configures Per-Consumer Limits (Priority: P2)

**Goal**: A platform operator can assign a tighter or higher rate-limit tier to a specific consumer via the Admin API without restarting Kong or disrupting other consumers. New consumers receive the default global policy automatically (FR-009).

**Independent Test**: Apply a consumer-scoped 2 req/s override to `consumer-b`, send 3 requests, assert the 3rd returns 429; clean up the override; confirm `smoke-test-consumer` is unaffected.

**Implements**: FR-008, FR-009 | SC-004, SC-006

### Implementation for User Story 3

- [x] T013 [US3] Add `_set_consumer_rate_limit()` helper to `scripts/seed-kong.sh` — signature: `_set_consumer_rate_limit <username> <second> <minute> <hour>`; (1) fetch existing consumer-scoped plugin ID via `GET /consumers/{username}/plugins?name=rate-limiting`, (2) if found PATCH the existing plugin ID, (3) if absent POST to `/consumers/{username}/plugins` with `name=rate-limiting` and the supplied values; include an inline comment: `# Operator usage: _set_consumer_rate_limit enterprise-user 50 1500 50000`

- [x] T014 [P] [US3] Add a `probe_custom_consumer_limit()` section to `scripts/smoke-test.sh` — (1) call the Admin API to create a consumer-scoped rate-limiting plugin on `consumer-b` with `config.second=2`, capturing the plugin ID from the JSON response, (2) send 3 rapid requests using `consumer-b-test-key`, (3) assert the 3rd request returns 429, (4) clean up by deleting the plugin via `DELETE /consumers/consumer-b/plugins/{id}`, (5) verify `smoke-test-consumer` still receives 200 (global default unaffected); call from the main smoke-test flow

**Checkpoint**: US3 is complete. Consumer-scoped overrides are verifiable via Admin API; global default remains unchanged for all other consumers.

---

## Phase 6: User Story 4 — Consumers Observe Remaining Quota (Priority: P3)

**Goal**: Every 200 response carries `RateLimit-Limit-*`, `RateLimit-Remaining-*`, and `RateLimit-Reset-*` headers for all three windows. Every 429 response carries `Retry-After`. This is provided automatically by Kong when `hide_client_headers=false` (set in T005).

**Independent Test**: Make one authenticated request; assert all nine quota headers are present. Trigger a 429 and assert `Retry-After` is present.

**Implements**: FR-006, FR-004 | SC-007

### Implementation for User Story 4

- [x] T015 [US4] Add `probe_quota_headers()` section to `scripts/smoke-test.sh` — (1) send one authenticated `GET /v1/models` request with `curl -si` and capture the headers, (2) assert all nine headers are present using nine separate `grep -i` checks: `ratelimit-limit-second`, `ratelimit-remaining-second`, `ratelimit-reset-second`, `ratelimit-limit-minute`, `ratelimit-remaining-minute`, `ratelimit-reset-minute`, `ratelimit-limit-hour`, `ratelimit-remaining-hour`, `ratelimit-reset-hour`, (3) trigger a 429 by sending 11 rapid requests and assert `retry-after:` is present on the throttled response, (4) exit non-zero if any header is missing; call from the main smoke-test flow

**Checkpoint**: All nine quota headers confirmed on 200 responses. Retry-After confirmed on 429. US4 is complete and independently testable with a single curl call.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Add the Prometheus alert for Redis unavailability (FR-012 fail-open notification) and run the full smoke suite to confirm end-to-end correctness.

- [x] T016 [P] Add a `rate_limiting` alert rule group to `services/prometheus/rules.yml` with one alert `RateLimitStoreDown`: `expr: up{job="redis"} == 0`, `for: 0m`, `labels: {severity: warning}`, `annotations: {summary: "Rate-limit counter store (Redis) unreachable — fail-open mode active", description: "Kong cannot reach Redis for rate-limit counter reads. All requests pass through without enforcement (fault_tolerant=true). Operator action required."}`; add an inline comment above the expr: `# fallback: use increase(kong_counter_total{name="redis_errors"}[1m]) > 0 if up{job="redis"} is not available`

- [ ] T017 Run `make smoke` from the repository root and confirm all existing probes plus all five new probes (T009, T010, T012, T014, T015) pass and the script exits 0

- [ ] T018 [P] Verify per-consumer Prometheus metrics by running `curl -s http://localhost:8001/metrics | grep 'kong_http_requests_total' | grep 'consumer='` after at least one request has been processed; confirm lines with `consumer="smoke-test-consumer"` appear with both `status_code="200"` and `status_code="429"` variants, satisfying SC-007

**Checkpoint**: `make smoke` exits 0. Prometheus alert rule present. Kong per-consumer metrics confirmed in Prometheus scrape output. Feature is complete.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately; T002 and T003 run in parallel with T001
- **Foundational (Phase 2)**: Depends on Phase 1 reads completing; T006 runs in parallel with T005; T007 depends on T005; T008 depends on T007 — **blocks all user stories**
- **US1 (Phase 3)**: Depends on Phase 2 (plugin must be installed); T009 and T010 run in parallel
- **US2 (Phase 4)**: Depends on Phase 2; T011 (seed-kong) must complete before T012 (smoke test can create consumer-b)
- **US3 (Phase 5)**: Depends on Phase 2; T013 and T014 run in parallel (different files); T014 depends on consumer-b existing (T011)
- **US4 (Phase 6)**: Depends only on Phase 2 (`hide_client_headers=false` is set in T005); can start in parallel with US2 and US3
- **Polish (Phase 7)**: Depends on all user story phases completing; T016 and T018 run in parallel with T017

### User Story Independence

| Story | Depends on | Can run in parallel with |
|---|---|---|
| US1 (Phase 3) | Phase 2 | US4 |
| US2 (Phase 4) | Phase 2 | US3, US4 |
| US3 (Phase 5) | Phase 2 + T011 (consumer-b) | US4 |
| US4 (Phase 6) | Phase 2 only | US2, US3 |

---

## Parallel Example: Phase 3 + Phase 6 (US1 + US4)

```
# Both can proceed after Phase 2 completes (T005–T008):
Task A: T009 — Add rate_limit_burst probe to scripts/smoke-test.sh
Task B: T010 — Add retry_after_header probe to scripts/smoke-test.sh
Task C: T015 — Add quota_headers probe to scripts/smoke-test.sh  ← US4, separate section
# T009/T010 edit US1 section; T015 edits a different US4 section — no conflict
```

---

## Implementation Strategy

### MVP First (US1 + US2 — Phases 1→4)

1. Complete Phase 1: Read existing files (T001–T003)
2. Complete Phase 2: Install rate-limiting + prometheus plugins (T004–T008)
3. Complete Phase 3: Burst test + Retry-After probe (T009–T010)
4. Complete Phase 4: Consumer-B provisioning + isolation probe (T011–T012)
5. **STOP and VALIDATE**: `make smoke` passes all four new probes — core throttle and isolation guarantees are live
6. **MVP is shippable**

### Full Delivery (add US3, US4, Polish)

1. MVP above
2. Phase 5: Consumer-scoped limit override helper + probe (T013–T014)
3. Phase 6: Quota headers probe (T015)
4. Phase 7: Prometheus alert rule + final smoke run + metrics verification (T016–T018)

---

## Acceptance Criteria Checklist

Per spec success criteria — verify before marking the feature done:

- [ ] **SC-001**: 429 arrives within same round-trip latency as a 200 (no visible added delay) — confirmed by T009 burst loop timing
- [ ] **SC-002**: Consumer B achieves 100% success rate while Consumer A is fully throttled — verified by T012
- [ ] **SC-003**: Same API key's 11th-per-second request is rejected regardless of which Kong instance handles it — guaranteed by shared Redis counter store (single source of truth)
- [ ] **SC-004**: Operator can change consumer tier via Admin API; new limit enforced on very next request with no restart — verified by T014
- [ ] **SC-005**: No counter bleed between consumers — verified by T012 isolation test
- [ ] **SC-006**: Consumer with default policy (no explicit config) is rejected on 11th request/second — verified by T009
- [ ] **SC-007**: `kong_http_requests_total{consumer=...}` metrics appear within one scrape interval — verified by T018

---

## Notes

- Redis host is `redis-cache` (the Docker Compose service name) — the plan/research docs say `redis` but the actual `docker-compose.yml` names the service `redis-cache`; all Kong plugin configs use the correct name
- `services/prometheus/prometheus.yml` and `services/prometheus/rules.yml` were created (not pre-existing) — they are config files for the planned Prometheus service, no container was added
- All other changes are additive to two existing files (`scripts/seed-kong.sh`, `scripts/smoke-test.sh`)
- `make seed-kong` is idempotent; re-running after any Phase 2 change is safe and recommended
- The `limit_by=consumer` config means `key-auth` (phase 012, priority 1003) always runs before `rate-limiting` (priority 901) — authenticated consumer context is always available when rate-limiting fires
- Fixed windows (not sliding) — see `research.md §1` for rationale; boundary burst of up to 20 req/s at window straddle is an accepted trade-off at 10 req/s scale
- `fault_tolerant=true` means Redis going down causes fail-open (no rate limits enforced); T016 adds the Prometheus alert that notifies operators of this condition
- Redis key namespace `ratelimit:*` is distinct from LiteLLM's `litellm_cache:*` — no collision risk on the shared Redis instance
