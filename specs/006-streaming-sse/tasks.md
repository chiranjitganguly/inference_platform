# Tasks: Streaming Chat Completions

**Input**: Design documents from `specs/006-streaming-sse/`

**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/openapi.yaml ✓, quickstart.md ✓

**Tests**: Included — plan.md explicitly requires a new `tests/contract/test_streaming.py` and smoke test extension.

**Organization**: US1 (streaming response) is the MVP. US2–US4 are independent of each other but share the contract test file; write sequentially to avoid conflicts. US3 and US4 have no obs-profile dependency — they pass on core only.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete tasks)
- **[Story]**: US1–US4 as labelled in spec.md
- Exact file paths in every task

---

## Phase 1: Setup (Verify Pre-Conditions)

**Purpose**: Confirm the existing stack is wired correctly before making changes. No new files created here.

- [x] T001 Verify `services/litellm/config.yaml` has `cache: true` under `litellm_settings:` (or confirm caching is active) — if absent, no cache_params change is needed; note finding before proceeding to T002
- [x] T002 Verify `services/kong/kong.yml` litellm-proxy route includes `POST` in methods and `strip_path: false` — streaming chunks must reach LiteLLM with the full path intact (should already be correct from feature 002)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The single config change that enables cache bypass for streaming. US1 end-to-end verification depends on this being deployed before testing.

**⚠️ CRITICAL**: T003 must complete before any streaming request is sent in test or smoke runs — without it, a streaming response may be erroneously cached and served as a non-streaming response.

- [x] T003 Add explicit `cache_params.supported_call_types` list to `litellm_settings:` in `services/litellm/config.yaml` that includes `acompletion`, `completion`, `aembedding`, `embedding` but NOT streaming variants — this enforces FR-013 (streaming responses must never be written to or served from the response cache); if `cache: true` is not currently set, add it alongside the cache_params block

**Checkpoint**: `make restart svc=litellm` then confirm via `make logs svc=litellm` that LiteLLM starts without config errors.

---

## Phase 3: User Story 1 — Receive a Streaming Chat Response (P1) 🎯 MVP

**Goal**: `POST /v1/chat/completions` with `stream: true` returns HTTP 200 with `Content-Type: text/event-stream`; each chunk is independently parseable JSON; stream ends with `data: [DONE]`; first chunk arrives within 2 seconds.

**Independent Test**: `curl --no-buffer` with `stream: true`; verify Content-Type header; read all `data:` lines; last line is `data: [DONE]`; concatenated `delta.content` is non-empty; `curl -w '%{time_starttransfer}'` < 2.0 s.

### Contract Tests for US1

- [x] T004 [US1] Create `tests/contract/test_streaming.py` with `test_streaming_response_status_and_content_type`: POST with `stream: true` and model `gpt-4o-mini`; assert HTTP 200; assert response header `Content-Type` starts with `text/event-stream`; assert response header `X-Platform` equals `inference-platform`; assert `X-API-Version` equals `1`; assert `X-Request-ID` is present
- [x] T005 [US1] Add `test_streaming_chunk_format` to `tests/contract/test_streaming.py`: read all `data:` lines from the SSE stream; for each line that is not `[DONE]`, parse as JSON; assert `object == "chat.completion.chunk"`; assert `choices[0].delta` key exists; assert each chunk is independently parseable (parse without prior chunk context)
- [x] T006 [US1] Add `test_streaming_done_sentinel` to `tests/contract/test_streaming.py`: read all `data:` lines; assert the final line equals `[DONE]`; assert at least one intermediate chunk has `choices[0].delta.content` as a non-empty string when all deltas are concatenated
- [x] T007 [US1] Add `test_streaming_ttft` to `tests/contract/test_streaming.py`: POST with `stream: true`; use `requests` with `stream=True` and measure time from request send to receipt of first chunk using `time.monotonic()`; assert elapsed time < 2.0 s
- [x] T008 [US1] Add `test_streaming_finish_reason` to `tests/contract/test_streaming.py`: read all chunks; assert all intermediate chunks have `choices[0].finish_reason == null`; assert the final content chunk (just before DONE) has `choices[0].finish_reason` in `{"stop", "length", "content_filter"}`

### Implementation for US1

- [x] T009 [US1] Add authenticated streaming probe to `scripts/smoke-test.sh`: use `curl --no-buffer -N` with `stream: true`; capture output; verify HTTP 200 by checking no `4xx` status; verify `Content-Type: text/event-stream`; verify at least one `data:` line is present; verify last `data:` line equals `[DONE]`; add TTFT check via separate `curl -s -o /dev/null -w '%{time_starttransfer}'` assertion < 2.0 inside the `if [[ -n "$SMOKE_API_KEY" ]]` block

**Checkpoint**: `pytest tests/contract/test_streaming.py -k "us1 or streaming_response or chunk_format or done_sentinel or ttft or finish_reason"` passes; `make smoke` shows streaming probe `[PASS]`.

---

## Phase 4: User Story 2 — Non-Streaming Requests Are Unaffected (P1)

**Goal**: Existing non-streaming callers receive identical responses after streaming is enabled. `tests/contract/test_chat_completions.py` passes without modification.

**Independent Test**: Run `pytest tests/contract/test_chat_completions.py` without any changes to that file; all tests pass (zero regressions, SC-003).

### Contract Tests for US2

- [x] T010 [US2] Add `test_non_streaming_unchanged` to `tests/contract/test_streaming.py`: POST with `stream` field absent; assert HTTP 200; assert `Content-Type` is `application/json` (not `text/event-stream`); assert `choices[0].message.content` is a non-empty string; assert `usage.prompt_tokens > 0` and `usage.completion_tokens > 0`
- [x] T011 [P] [US2] Add `test_stream_false_explicit` to `tests/contract/test_streaming.py`: POST with `stream: false` explicitly set; assert same complete JSON response shape as `stream` omitted; assert no SSE formatting in response body

### Implementation for US2

- [ ] T012 [US2] Run `pytest tests/contract/test_chat_completions.py` and confirm all existing tests pass — this is the regression gate for SC-003; fix any failures before marking T012 done (no source changes expected; this is a verification task only)

**Checkpoint**: `pytest tests/contract/test_chat_completions.py` exits 0 with no changes to the file.

---

## Phase 5: User Story 3 — Streaming Requests Respect Authentication (P1)

**Goal**: Streaming requests without a valid API key are rejected at Kong with HTTP 401. No SSE body is sent.

**Independent Test**: POST with `stream: true` and no `Authorization` header; verify HTTP 401; verify `Content-Type` is NOT `text/event-stream`.

### Contract Tests for US3

- [x] T013 [US3] Add `test_streaming_unauthenticated_returns_401` to `tests/contract/test_streaming.py`: POST with `stream: true` and NO `Authorization` header; assert HTTP 401; assert response `Content-Type` is not `text/event-stream`; assert no `data:` lines in response body
- [x] T014 [P] [US3] Add `test_streaming_invalid_key_returns_401` to `tests/contract/test_streaming.py`: POST with `stream: true` and `Authorization: Bearer invalid-key-xyz`; assert HTTP 401

**Checkpoint**: `pytest tests/contract/test_streaming.py -k "401 or unauthenticated or invalid_key"` passes on core profile (no obs needed).

---

## Phase 6: User Story 4 — Invalid Inputs Return 400 on Streaming Requests (P2)

**Goal**: Unknown model name with `stream: true` returns HTTP 400 with structured error body in < 50 ms. Empty messages with `stream: true` returns HTTP 400.

**Independent Test**: POST with `stream: true` and `model: "nonexistent-model-xyz"`; verify HTTP 400; verify error body contains the model name; verify elapsed time < 0.5 s.

### Contract Tests for US4

- [x] T015 [US4] Add `test_streaming_invalid_model_returns_400` to `tests/contract/test_streaming.py`: POST with `stream: true`, `model: "nonexistent-model-xyz"`, one message; assert HTTP 400; assert `"nonexistent-model-xyz"` in response body text; measure elapsed with `time.monotonic()` and assert < 0.5 s; assert `Content-Type` is NOT `text/event-stream`
- [x] T016 [P] [US4] Add `test_streaming_empty_messages_returns_400` to `tests/contract/test_streaming.py`: POST with `stream: true`, valid model, `messages: []`; assert HTTP 400

**Checkpoint**: `pytest tests/contract/test_streaming.py -k "400 or invalid_model or empty_messages"` passes on core profile.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Full suite validation, cache bypass verification, TTFT metric confirmation.

- [ ] T017 Run full contract suite `pytest tests/contract/` and confirm all tests pass or skip correctly (Phoenix/Langfuse tests skip on core-only, all others pass)
- [x] T018 [P] Add unauthenticated streaming probe to `scripts/smoke-test.sh` (outside the `SMOKE_API_KEY` block): POST with `stream: true` and no auth header; assert HTTP 401 — verifies Kong rejects streaming without auth
- [ ] T019 [P] Manually verify cache bypass using quickstart.md Scenario 5 (same prompt twice; both TTFT values > 0.1 s, confirming no cache hit on streaming)
- [ ] T020 [P] Verify `litellm_request_latency_seconds` Prometheus histogram is populated after streaming requests: `curl -s 'http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,rate(litellm_request_latency_seconds_bucket{stream="true"}[5m]))'` returns a non-null value (requires Prometheus running)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — verify immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — BLOCKS streaming cache behaviour verification
- **Phase 3 (US1)**: Depends on Phase 2 (cache bypass active); contract tests can be written before Phase 2 but must be run after
- **Phase 4 (US2)**: Depends on Phase 3 completion (conftest.py fixtures in place); regression run can start immediately
- **Phase 5 (US3)**: Depends on Phase 3 (conftest.py); independent of Phase 4
- **Phase 6 (US4)**: Depends on Phase 3 (conftest.py); independent of Phase 4/5
- **Phase 7 (Polish)**: Depends on all story phases

### User Story Dependencies

- **US1 (P1)**: Foundational (T003) must complete before cache behaviour is verified
- **US2 (P1)**: Independent of US1 behaviour; shares conftest.py fixtures
- **US3 (P1)**: Independent of US1/US2; core profile only
- **US4 (P2)**: Independent of US1–US3; core profile only

### Within Each User Story

- Contract tests (T004–T008) can be written before Phase 2 completes, but run after
- Smoke extension (T009) should be written after T004 to reuse the verified streaming logic
- T012 (regression run) must be run against a live stack to be meaningful

### Parallel Opportunities

- T004–T008 (US1 contract tests): all go in the same file — write sequentially; each is a new function
- T010 and T011 (US2 tests): different function names in same file — write together
- T013 and T014 (US3 tests): write together (both auth-related, same file section)
- T015 and T016 (US4 tests): write together (both 400-related, same file section)
- T017, T018, T019, T020 (Polish): independent — run in parallel

---

## Parallel Example: Writing All Contract Tests (Phase 3–6)

```text
# All contract tests live in one file — plan content in parallel, write sequentially:

tests/contract/test_streaming.py functions (in order):
  test_streaming_response_status_and_content_type  ← T004
  test_streaming_chunk_format                       ← T005
  test_streaming_done_sentinel                      ← T006
  test_streaming_ttft                               ← T007
  test_streaming_finish_reason                      ← T008
  test_non_streaming_unchanged                      ← T010
  test_stream_false_explicit                        ← T011
  test_streaming_unauthenticated_returns_401        ← T013
  test_streaming_invalid_key_returns_401            ← T014
  test_streaming_invalid_model_returns_400          ← T015
  test_streaming_empty_messages_returns_400         ← T016
```

---

## Implementation Strategy

### MVP First (US1 — Streaming Response)

1. Complete Phase 1: verify existing config
2. Complete Phase 2: add `cache_params.supported_call_types` to `services/litellm/config.yaml`
3. Complete Phase 3: write `tests/contract/test_streaming.py` (T004–T008) + smoke probe (T009)
4. **STOP and VALIDATE**: `make smoke` streaming probe passes; TTFT < 2 s observed
5. Feature is live and verified — US2–US4 add test coverage

### Incremental Delivery

1. Foundation → streaming works, cache bypassed
2. US1 → contract tests green + smoke passes → MVP complete
3. US2 → regression tests confirm no breakage
4. US3 → auth tests confirm Kong enforces key-auth on streams
5. US4 → validation tests confirm fast 400 on bad input
6. Polish → full suite green, TTFT metric confirmed in Prometheus

---

## Notes

- All contract tests go in `tests/contract/test_streaming.py` — the existing `test_chat_completions.py` must not be modified (SC-003)
- `requests` library with `stream=True` is used to read SSE responses without buffering the full body
- TTFT in contract tests uses `time.monotonic()` around `requests.post(..., stream=True)` and `next(response.iter_lines())`
- The `smoke-test.sh` streaming probe uses `curl --no-buffer -N` to prevent curl buffering SSE output
- No new services, no new Docker images — the only infra change is T003 (config.yaml cache_params)
- `[P]` markers on same-file tasks mean "content can be planned in parallel" but writes must be sequential to avoid YAML/Python merge conflicts
