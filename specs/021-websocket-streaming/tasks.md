# Tasks: WebSocket Streaming

**Input**: Design documents from `specs/021-websocket-streaming/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/ ✅ | quickstart.md ✅

**Organization**: Tasks grouped by user story — each phase is independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks in the same phase)
- **[USn]**: User story from spec.md
- Exact file paths included in all task descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Inject new environment variables; no code changes yet.

- [x] T001 Add `WS_MAX_CONNECTIONS_PER_KEY: ${WS_MAX_CONNECTIONS_PER_KEY:-10}` and `WS_IDLE_TIMEOUT_SECONDS: ${WS_IDLE_TIMEOUT_SECONDS:-300}` to the `guardrails` service environment block in `docker-compose.yml`
- [x] T002 [P] Add `WS_MAX_CONNECTIONS_PER_KEY=` and `WS_IDLE_TIMEOUT_SECONDS=` (empty values) to `.env.example`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Kong route, app-state initialisation, and module skeleton — must be complete before any user-story work can be tested end-to-end.

**⚠️ CRITICAL**: No user story implementation can be tested until this phase is complete.

- [x] T003 Add service `litellm-ws` (url: `http://guardrails:8088`, connect_timeout: 5000, read_timeout: 120000, write_timeout: 120000) and route `ws-chat-completions` (paths: `/ws/v1/chat/completions`, protocols: `[http, ws]`, strip_path: false, key-auth plugin with `hide_credentials: false`) to `services/kong/kong.yml`
- [x] T004 Extend `_lifespan()` in `services/guardrails/main.py` to initialise `app.state.ws_connections: dict[str, int]` (empty dict) and `app.state.ws_lock: asyncio.Lock` before the existing cache loads
- [x] T005 [P] Create `services/guardrails/websocket.py` with: module-level env-var reads (`WS_MAX_CONNECTIONS_PER_KEY`, `WS_IDLE_TIMEOUT_SECONDS`), `async def acquire_connection(app_state, consumer_id: str) -> bool` (lock-guarded increment returning False if limit reached), and `async def release_connection(app_state, consumer_id: str) -> None` (lock-guarded decrement floored at 0)
- [x] T006 Import `ws_chat_completions` from `websocket.py` and register it with `app.add_api_websocket_route` in `services/guardrails/main.py`

**Checkpoint**: `make up-core && make seed-kong` — Kong route exists; guardrails starts cleanly.

---

## Phase 3: User Story 1 — Real-Time Token Streaming (Priority: P1) 🎯 MVP

**Goal**: A caller connects, sends one chat completion payload, and receives OpenAI streaming delta JSON frames followed by a `stream_complete` sentinel.

**Independent Test**: `wscat -c ws://localhost:8080/ws/v1/chat/completions -H "Authorization: $SMOKE_API_KEY"` → send one payload → verify `chat.completion.chunk` frames arrive → verify `{"type":"stream_complete"}` is the final frame.

- [x] T007 [US1] Implement `ws_chat_completions(websocket: WebSocket) -> None` handler skeleton in `services/guardrails/websocket.py`: extract `X-Consumer-Username` from `websocket.headers`, call `acquire_connection`; if False send `connection_limit_exceeded` JSON and `await websocket.close(4029)`; otherwise `await websocket.accept()` and initialise `is_streaming: bool = False`
- [x] T008 [US1] Add the outer receive loop in `ws_chat_completions` in `services/guardrails/websocket.py`: `asyncio.wait_for(websocket.receive_text(), timeout=WS_IDLE_TIMEOUT_SECONDS)` inside a `while True` loop; wrap the whole handler in a `try/finally` that always calls `release_connection`
- [x] T009 [US1] Implement JSON payload parsing in the receive loop in `services/guardrails/websocket.py`: `json.loads(raw_text)` — on `json.JSONDecodeError` or missing `model`/`messages` keys send `{"type":"validation_error","error":"validation_error","message":"...","detail":{}}` and `continue`
- [x] T010 [US1] Integrate the three existing validation gates into the per-message handler in `services/guardrails/websocket.py`: import vision/FC/SO gate functions directly from their submodules; call each in sequence; on a gate rejection send `{"type":"validation_error",...}` and `continue`
- [x] T011 [US1] Implement `async def _stream_to_ws(websocket, body: bytes, headers: dict, request_id: str) -> tuple[int, bool]` in `services/guardrails/websocket.py`: force `body["stream"] = True`; use `httpx.AsyncClient` with `stream=True` to POST to `LITELLM_BASE_URL/v1/chat/completions`; iterate response lines; for each `data: {…}` line call `await websocket.send_text(chunk_json)`; stop iteration on `data: [DONE]`
- [x] T012 [US1] Send `{"type":"stream_complete"}` via `await websocket.send_text(...)` immediately after the `[DONE]` sentinel is consumed in `_stream_to_ws` in `services/guardrails/websocket.py`
- [x] T013 [P] [US1] Add OpenTelemetry CHAIN span per stream request in `services/guardrails/websocket.py`: `tracer.start_as_current_span("ws.stream_request")` wrapping the `_stream_to_ws` call; set span attributes `ws.request_id`, `ws.model`, `ws.consumer_id`; close span in `finally` block (no prompt content in attributes)
- [x] T014 [P] [US1] Call `_write_ws_audit(...)` with `event_type="ws_stream_request"` after each stream completes or errors in `services/guardrails/websocket.py`
- [x] T015 [US1] Write contract test `test_single_stream` in `tests/contract/test_ws_streaming.py`: connect with valid API key, send one payload, assert received frames each have `"object": "chat.completion.chunk"`, assert at least one frame has `choices[0].finish_reason` set, assert final frame is `{"type": "stream_complete"}`

**Checkpoint**: US1 independently testable — `wscat` session delivers token deltas and `stream_complete`.

---

## Phase 4: User Story 4 + User Story 2 — Authentication + Persistent Connection (Priority: P2)

**Goal**: Connection-limit enforcement (US4) and verified connection reuse across multiple sequential requests (US2).

**Independent Test (US4)**: `wscat` without auth header → HTTP 401. Open 11 connections → 11th rejected.

**Independent Test (US2)**: Single `wscat` session delivers two sequential streams without reconnecting.

- [x] T016 [US2] [US4] Complete the `acquire_connection` guard in `ws_chat_completions` in `services/guardrails/websocket.py`: ensure the `connection_limit_exceeded` path sends the structured JSON error before calling `websocket.close(4029)`
- [x] T017 [US2] Add the `is_streaming` guard in the receive loop in `services/guardrails/websocket.py`: before calling `_stream_to_ws`, if `is_streaming is True` send `stream_in_progress` JSON error and `continue` (do not close)
- [x] T018 [P] [US2] Write contract test `test_persistent_connection` in `tests/contract/test_ws_streaming.py`: open one connection, send payload A, consume all frames until `stream_complete`, send payload B on the same connection, consume all frames until `stream_complete` — assert no `websocket.closed` in between
- [x] T019 [P] [US4] Write contract test `test_connect_unauthenticated` in `tests/contract/test_ws_streaming.py`: attempt to connect without `Authorization` header — assert `InvalidStatus` with code 401
- [x] T020 [US2] Write contract test `test_stream_in_progress` in `tests/contract/test_ws_streaming.py`: send second payload during active stream, assert `stream_in_progress` error, assert first stream completes
- [x] T021 [US4] Write contract test `test_connection_limit` in `tests/contract/test_ws_streaming.py` — connection limit enforcement (open 11 connections, assert 11th rejected)

**Checkpoint**: Per-key connection limits enforced; same `wscat` session handles multiple requests without reconnect.

---

## Phase 5: User Story 3 — Graceful Error + Disconnection Handling (Priority: P3)

**Goal**: All error conditions return structured JSON over the open connection; abrupt disconnection is cleaned up without resource leaks.

**Independent Test**: Send malformed payload → receive `validation_error` JSON → send valid payload → receive full stream. Then close the connection mid-stream → `make stats` shows no memory growth.

- [x] T022 [US3] Implement `model_error` and `rate_limit_error` error paths in `_stream_to_ws` in `services/guardrails/websocket.py`: if LiteLLM responds with a non-200 status before any SSE data is sent, send the appropriate JSON error frame and return (do not close the WebSocket)
- [x] T023 [US3] Implement `stream_error` path in `_stream_to_ws` `except` block in `services/guardrails/websocket.py`: on `httpx.HTTPError` send `stream_error` JSON and reset `is_streaming = False`
- [x] T024 [US3] Implement idle-timeout path in the receive loop in `services/guardrails/websocket.py`: catch `asyncio.TimeoutError`; send `connection_closing` JSON; then `await websocket.close(1001)` and `break`
- [x] T025 [US3] Implement `WebSocketDisconnect` handler in the receive loop `except` block in `services/guardrails/websocket.py`: log metadata-only entry; the `finally` block calls `release_connection`
- [x] T026 [P] [US3] Write contract test `test_invalid_payload` in `tests/contract/test_ws_streaming.py`: send malformed JSON, assert `validation_error` received, assert connection stays open, assert second valid request streams correctly
- [x] T027 [P] [US3] Write contract test `test_model_error` in `tests/contract/test_ws_streaming.py`: request a non-existent model, assert error frame received, assert connection is still open

**Checkpoint**: All error paths confirmed; `docker stats` shows no guardrails memory growth after 100 connect/disconnect cycles.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Code quality gates, smoke test automation, and acceptance criteria sign-off.

- [x] T028 [P] Create `tests/smoke/test_websocket_streaming.sh`: non-interactive `wscat --execute` tests for Test 1 (basic stream → assert `stream_complete` in output), Test 2 (unauthenticated → expect non-zero exit), and Test 3 (malformed payload → assert `validation_error` in output)
- [x] T029 [P] Run `ruff check services/guardrails/` — zero warnings across `main.py` and `websocket.py`
- [x] T030 [P] Run `mypy services/guardrails/websocket.py --ignore-missing-imports` — zero type errors; all function signatures have complete type annotations
- [ ] T031 Validate acceptance criteria SC-001 through SC-009 using the `wscat` commands and queries in `specs/021-websocket-streaming/quickstart.md` — record pass/fail against each criterion
- [ ] T032 Run `make smoke` to confirm the full platform smoke test still passes after all changes

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Phase 1 completion. **Blocks all user story phases.**
- **US1 (Phase 3)**: Depends on Phase 2. No dependency on US2, US3, or US4.
- **US4+US2 (Phase 4)**: Depends on Phase 2. Builds on the handler skeleton from Phase 3.
- **US3 (Phase 5)**: Depends on Phase 3 (error paths extend the handler from US1).
- **Polish (Phase 6)**: Depends on all prior phases.

### User Story Dependencies

| Story | Can start after | Depends on other stories? |
|---|---|---|
| US1 (P1) | Phase 2 complete | None |
| US4+US2 (P2) | Phase 2 complete | Needs US1 handler skeleton to exist (T007–T008) |
| US3 (P3) | Phase 3 complete | Extends `_stream_to_ws` and receive-loop from US1 |

### Within Each User Story

- Foundational module tasks (T003–T006) before any handler code
- Handler skeleton (T007–T008) before integration tasks (T009–T014)
- Implementation complete before contract tests (T015, T018–T021, T026–T027)
- All contract tests before smoke test (T028)

---

## Parallel Opportunities

### Phase 2 Parallel

```
T003 (kong.yml)  ←── independent
T005 (websocket.py skeleton)  ←── independent
T001+T002 (env vars)  ←── independent
```

`T004` and `T006` both touch `main.py` — do not parallelise.

### Phase 3 Parallel

```
T013 (OTel span)       ←── different concern from SSE bridge
T014 (_write_audit)    ←── different concern from SSE bridge
```

`T007 → T008 → T009 → T010 → T011 → T012` must be sequential.

### Phase 6 Parallel

```
T028 (smoke test)   ←── independent
T029 (ruff)         ←── independent
T030 (mypy)         ←── independent
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001–T002)
2. Complete Phase 2: Foundational (T003–T006) — **CRITICAL**
3. Complete Phase 3: US1 (T007–T015)
4. **STOP AND VALIDATE**: `wscat` delivers token deltas and `stream_complete` on a single request
5. Ship MVP — callers can now stream tokens

### Incremental Delivery

1. Setup + Foundational → Kong WS route live, guardrails module wired
2. US1 → token streaming works end-to-end (MVP)
3. US4+US2 → per-key connection limits + multi-request sessions
4. US3 → production-grade error handling + resource cleanup
5. Polish → code quality gates + automated smoke tests

---

## Notes

- `[P]` tasks touch different files or concerns — safe to run concurrently
- `[USn]` label maps every task to its user story for traceability
- `websocket.py` is the only new file; `main.py` and `kong.yml` receive surgical additions
- `hide_credentials: false` on the Kong WS route is intentional — guardrails reads `X-Consumer-Username` for connection-limit tracking; the API key is never forwarded to LiteLLM
- Streaming responses from LiteLLM are never cached (existing `cache_params.supported_call_types` in `litellm/config.yaml` already excludes streaming — no change needed)
- Commit after each phase checkpoint for easy rollback
