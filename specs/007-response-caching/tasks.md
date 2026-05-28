# Tasks: Response Caching Layer

**Input**: Design documents from `specs/007-response-caching/`

**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/openapi.yaml ✓, quickstart.md ✓

**Tests**: Included — plan.md specifies a new `tests/contract/test_caching.py` and smoke test extension.

**Organization**: US1 (cache hit) and US2 (cache miss) are both P1 and form the correctness pair — US1 first as MVP, US2 immediately after. US3 (TTL) and US4 (streaming bypass) are P2 and independent of each other. All contract tests go in the same file — write sequentially.

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete tasks)
- **[Story]**: US1–US4 as labelled in spec.md
- Exact file paths in every task

---

## Phase 1: Setup (Verify Pre-Conditions)

**Purpose**: Confirm existing config state before making changes. No new files created here.

- [x] T001 Verify `services/litellm/config.yaml` has `cache: true` and `cache_params.type: redis` already present from feature 006 — if absent, note before proceeding to T003
- [x] T002 Verify `docker-compose.yml` does NOT yet have `redis-cache` or `redis-queue` service definitions — confirm both need to be added in T003/T004

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Redis infrastructure and LiteLLM connection config. US1–US4 contract tests can be written before this phase, but MUST NOT be run until Phase 2 is complete and `make up-core` is healthy.

**⚠️ CRITICAL**: T003–T007 must all complete before any cache behaviour can be tested.

- [x] T003 Add `redis-cache` service to `docker-compose.yml` under `services:` with `profiles: [core]`: image `redis:7.2-alpine`, `command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru --save "" --appendonly no`, `expose: ["6379"]`, `ports: ["6379:6379"]`, healthcheck `redis-cli ping`, `restart: unless-stopped` — no persistence (no volumes)
- [x] T004 Add `redis-queue` service to `docker-compose.yml` under `services:` with `profiles: [core]`: image `redis:7.2-alpine`, `command: redis-server --maxmemory-policy noeviction --save "" --appendonly no`, `expose: ["6380"]`, `ports: ["6380:6380"]`, healthcheck `redis-cli -p 6380 ping`, `restart: unless-stopped`
- [x] T005 Update `litellm` service in `docker-compose.yml` to add `redis-cache` dependency: add `redis-cache: {condition: service_healthy}` under its `depends_on:` block alongside the existing `postgres` dependency
- [x] T006 Add `redis_host: redis-cache`, `redis_port: 6379`, `namespace: llm_cache`, and `ttl: os.environ/LITELLM_CACHE_TTL` to `cache_params:` in `services/litellm/config.yaml` — keep existing `type: redis` and `supported_call_types` from feature 006 unchanged
- [x] T007 Add `LITELLM_CACHE_TTL=` (name only, no value) to the `# ── Redis ──` section of `.env.example` with a comment `# Response cache TTL in seconds (default: 3600)`

**Checkpoint**: `make up-core` → `make logs svc=litellm` shows no config errors; `make logs svc=redis-cache` shows Redis started and accepting connections; `docker exec <litellm-container> redis-cli -h redis-cache ping` returns `PONG`.

---

## Phase 3: User Story 1 — Cache Hit Returns Stored Response (P1) 🎯 MVP

**Goal**: `POST /v1/chat/completions` with identical model, messages, and temperature on second call returns `x-litellm-cache-hit: True` in under 10 ms with no provider API call.

**Independent Test**: Send same request twice; assert second response header is `x-litellm-cache-hit: True`; assert second response body matches first; assert second response time < 10 ms.

### Contract Tests for US1

- [x] T008 [US1] Create `tests/contract/test_caching.py` with `test_cache_hit_returns_identical_response`: POST `{"model":"gpt-4o-mini","messages":[{"role":"user","content":"What is 2+2?"}],"temperature":0.0}` twice with same `auth_headers`; assert first response `x-litellm-cache-hit` is `False` or absent; assert second response `x-litellm-cache-hit` is `True`; assert second `choices[0].message.content` equals first `choices[0].message.content`
- [x] T009 [US1] Add `test_cache_hit_latency_under_10ms` to `tests/contract/test_caching.py`: POST same cacheable request twice; measure second response time with `time.monotonic()` from request send to `resp.json()` receipt; assert elapsed < 0.010 s (10 ms)
- [x] T010 [US1] Add `test_cache_status_header_present_on_non_streaming` to `tests/contract/test_caching.py`: POST non-streaming request; assert `x-litellm-cache-hit` header is present in response headers (value is either `True` or `False`)
- [x] T011 [US1] Add `test_cache_hit_increments_prometheus_counter` to `tests/contract/test_caching.py`: GET `http://localhost:9090/api/v1/query?query=litellm_cache_hit_count` and record baseline; POST same cacheable request twice to produce one cache hit; GET Prometheus again; assert counter value increased by at least 1; skip if Prometheus not reachable at `http://localhost:9090`

### Implementation for US1

- [x] T012 [US1] Add authenticated cache-hit probe to `scripts/smoke-test.sh` inside the `if [[ -n "$SMOKE_API_KEY" ]]` block: POST `{"model":"gpt-4o-mini","messages":[{"role":"user","content":"cache smoke test"}],"temperature":0.0}` twice using `curl -s -D -`; capture response headers for each; assert second response headers contain `x-litellm-cache-hit: True`; label probes `[PASS]/[FAIL]` as `POST /v1/chat/completions cache — first request miss` and `POST /v1/chat/completions cache — second request hit`

**Checkpoint**: `pytest tests/contract/test_caching.py -k "cache_hit"` passes; `make smoke` shows cache miss + cache hit `[PASS]`.

---

## Phase 4: User Story 2 — Cache Miss Triggers Fresh Provider Call (P1)

**Goal**: Requests differing by model, messages, or temperature always call the provider and return `x-litellm-cache-hit: False`. No cross-key contamination.

**Independent Test**: POST request A (cached), then request B (different model), then request C (different messages), then request D (different temperature) — assert B, C, D all return `x-litellm-cache-hit: False`.

### Contract Tests for US2

- [x] T013 [US2] Add `test_cache_miss_different_model` to `tests/contract/test_caching.py`: POST `{"model":"gpt-4o-mini","messages":[{"role":"user","content":"What is 3+3?"}],"temperature":0.0}` (cache it), then POST same messages with `"model":"gpt-4o"`; assert second response `x-litellm-cache-hit` is `False` (different model = different cache key)
- [x] T014 [P] [US2] Add `test_cache_miss_different_messages` to `tests/contract/test_caching.py`: POST request with content `"Cache miss test A."` (cache it), then POST with content `"Cache miss test B."` (one character changed); assert second response `x-litellm-cache-hit` is `False`
- [x] T015 [P] [US2] Add `test_cache_miss_different_temperature` to `tests/contract/test_caching.py`: POST `{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Temperature key test."}],"temperature":0.0}` (cache it), then POST same model and messages with `"temperature":0.7`; assert second response `x-litellm-cache-hit` is `False`
- [x] T016 [P] [US2] Add `test_cache_miss_stored_for_next_hit` to `tests/contract/test_caching.py`: POST a unique cacheable request (unique content string); assert `x-litellm-cache-hit: False` (miss); POST the identical request immediately; assert `x-litellm-cache-hit: True` (stored and returned on next call)

**Checkpoint**: `pytest tests/contract/test_caching.py -k "cache_miss"` passes.

---

## Phase 5: User Story 3 — Cached Responses Expire Automatically (P2)

**Goal**: Cache entries expire after TTL elapses. Post-expiry identical requests return `x-litellm-cache-hit: False` and trigger a provider call.

**Independent Test**: Set `LITELLM_CACHE_TTL=30`, restart LiteLLM, send request (miss), wait 35 s, send same request (miss again); confirm via quickstart Scenario 6.

### Contract Tests for US3

- [x] T017 [US3] Add `test_cache_ttl_expiry` to `tests/contract/test_caching.py`: decorate with `@pytest.mark.skipif` that skips when `LITELLM_CACHE_TTL` env var is not set to a value ≤ 60; POST unique cacheable request, assert miss; sleep `int(os.environ["LITELLM_CACHE_TTL"]) + 5` seconds; POST same request again; assert `x-litellm-cache-hit: False` (entry expired); assert response is valid OpenAI JSON

### Implementation for US3

- [ ] T018 [P] [US3] Verify TTL expiry manually using quickstart.md Scenario 6: set `LITELLM_CACHE_TTL=30` in `.env`, run `make restart svc=litellm`, execute scenario curl commands, confirm third request (after 35s sleep) returns `x-litellm-cache-hit: False` — mark done when scenario passes end-to-end

**Checkpoint**: `pytest tests/contract/test_caching.py -k "ttl"` passes when `LITELLM_CACHE_TTL=30` is set; or skips gracefully when TTL > 60 s.

---

## Phase 6: User Story 4 — Non-Cacheable Requests Always Bypass Cache (P2)

**Goal**: Streaming requests (`stream: true`) never produce a cache hit and are never stored in the cache. `x-litellm-cache-hit` header is absent on SSE responses.

**Independent Test**: POST streaming request twice; assert neither response contains `x-litellm-cache-hit: True`; assert both are `Content-Type: text/event-stream`.

### Contract Tests for US4

- [x] T019 [US4] Add `test_streaming_bypass_no_cache_hit_header` to `tests/contract/test_caching.py`: POST `{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Streaming bypass test."}],"stream":true}` with `requests.post(..., stream=True)`; assert response `Content-Type` starts with `text/event-stream`; assert `x-litellm-cache-hit` header is NOT present or is NOT `True` in response headers; close response
- [x] T020 [P] [US4] Add `test_streaming_never_populates_cache` to `tests/contract/test_caching.py`: POST streaming request (unique content); POST identical non-streaming request immediately after; assert non-streaming response `x-litellm-cache-hit` is `False` (streaming did not write to cache — non-streaming must call provider, not serve from streaming-populated cache)

### Implementation for US4

- [x] T021 [US4] Add streaming cache bypass smoke probe to `scripts/smoke-test.sh` inside the `if [[ -n "$SMOKE_API_KEY" ]]` block: send streaming request with `curl -s --no-buffer -N -D /tmp/stream_headers.txt`; check `/tmp/stream_headers.txt` for `x-litellm-cache-hit`; assert header is absent or not `True`; label probe `POST /v1/chat/completions streaming — cache bypass confirmed`

**Checkpoint**: `pytest tests/contract/test_caching.py -k "streaming"` passes on core profile.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Full suite validation, memory budget check, Prometheus counter verification.

- [ ] T022 Run full contract suite `pytest tests/contract/` and confirm all tests pass or skip correctly (Phoenix/Langfuse tests skip on core-only, all cache tests pass)
- [ ] T023 [P] Run `make stats` after 10+ cache writes and verify `redis-cache` container RSS is within 256 MB limit; run `make smoke` and confirm all probes pass
- [ ] T024 [P] Verify `litellm_cache_hit_count` Prometheus counter is populated after cache-hit requests: `curl -s 'http://localhost:9090/api/v1/query?query=litellm_cache_hit_count'` returns a non-zero value (requires Prometheus running)
- [ ] T025 [P] Run quickstart.md Scenario 5 (Prometheus counter increment verification) end-to-end and confirm delta ≥ 1 after two identical requests

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — verify immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 verification — BLOCKS all US phases
- **Phase 3 (US1)**: Contract tests can be written before Phase 2 completes, but MUST be run after `make up-core` with Redis healthy
- **Phase 4 (US2)**: Depends on Phase 3 (conftest.py fixtures in place from US1); cache key correctness builds on US1 cache being active
- **Phase 5 (US3)**: Depends on Phase 3 (cache writing must work before TTL expiry can be tested); independent of Phase 4
- **Phase 6 (US4)**: Depends on Phase 3 (conftest.py); independent of Phase 4/5
- **Phase 7 (Polish)**: Depends on all story phases

### User Story Dependencies

- **US1 (P1)**: Foundational phase must complete (Redis running, LiteLLM connected)
- **US2 (P1)**: US1 infrastructure in place (cache must be writing entries to test misses)
- **US3 (P2)**: US1 in place (cache writes needed to test TTL expiry); independent of US2
- **US4 (P2)**: Independent of US1/US2/US3; requires `stream: true` support from feature 006

### Within Each User Story

- Contract tests (T008–T011, T013–T016, T017, T019–T020) can be written before Phase 2 completes, but run after
- Smoke test extensions (T012, T021) should be written after contract tests to reuse verified request payloads
- T018 (manual TTL verification) is runtime-only — cannot be automated without a live stack with short TTL

### Parallel Opportunities

- T003 and T004 (redis-cache, redis-queue): different service definitions, write in parallel
- T006 and T007 (.env.example, litellm depends_on): different files, write in parallel
- T013, T014, T015 (US2 cache miss tests): different test functions in same file — write sequentially; content plannable in parallel
- T019 and T020 (US4 streaming bypass tests): write sequentially (same file)
- T022, T023, T024, T025 (Polish): independent runtime checks — run in parallel

---

## Parallel Example: Writing All Contract Tests (Phases 3–6)

```text
tests/contract/test_caching.py functions (in order):
  test_cache_hit_returns_identical_response      ← T008
  test_cache_hit_latency_under_10ms              ← T009
  test_cache_status_header_present               ← T010
  test_cache_hit_increments_prometheus_counter   ← T011
  test_cache_miss_different_model                ← T013
  test_cache_miss_different_messages             ← T014
  test_cache_miss_different_temperature          ← T015
  test_cache_miss_stored_for_next_hit            ← T016
  test_cache_ttl_expiry                          ← T017
  test_streaming_bypass_no_cache_hit_header      ← T019
  test_streaming_never_populates_cache           ← T020
```

---

## Implementation Strategy

### MVP First (US1 — Cache Hit)

1. Complete Phase 1: verify existing config state
2. Complete Phase 2: add Redis services, update LiteLLM cache_params
3. Complete Phase 3: write `tests/contract/test_caching.py` (T008–T011) + smoke probe (T012)
4. **STOP and VALIDATE**: `make smoke` cache probes pass; `pytest tests/contract/test_caching.py -k "cache_hit"` green
5. Cache is live — US2–US4 add correctness and edge-case coverage

### Incremental Delivery

1. Foundation → Redis running, LiteLLM connected, cache active
2. US1 → cache hit tested, sub-10ms latency confirmed, Prometheus counter verified → MVP complete
3. US2 → correctness tests confirm no cross-key contamination
4. US3 → TTL expiry verified (manual + automated at short TTL)
5. US4 → streaming bypass confirmed via contract + smoke
6. Polish → full suite green, memory within budget

---

## Notes

- All contract tests go in `tests/contract/test_caching.py` — the existing `test_chat_completions.py` and `test_streaming.py` must not be modified
- Cache status header is `x-litellm-cache-hit` (lowercase, LiteLLM native) — assert case-insensitively in tests
- `time.monotonic()` is used for latency assertions — wrap only the HTTP call and response read, not connection setup
- T011 (Prometheus) and T024 (Prometheus Polish) both skip if Prometheus is unreachable — use `pytest.skip()` inside a try/except on the Prometheus GET
- T017 (TTL expiry contract test) skips by default unless `LITELLM_CACHE_TTL` is set ≤ 60 s to make the sleep tolerable in CI
- No new Docker images — the only infra additions are two Redis 7.2-alpine service definitions
- `[P]` markers on same-file tasks mean "content can be planned in parallel" but writes are sequential to avoid merge conflicts
