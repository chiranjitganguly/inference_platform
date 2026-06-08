# Tasks: Cache Flush Management

**Input**: Design documents from `specs/022-cache-flush-management/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/ ✅ | quickstart.md ✅

**Organization**: Tasks grouped by user story — each phase is independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks in the same phase)
- **[USn]**: User story from spec.md
- Exact file paths included in all task descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add `redis[asyncio]` dependency and wire up the Kong route — no endpoint logic yet.

- [x] T001 Add `redis[asyncio]==5.0.8` to `services/portal-backend/requirements.txt`
- [x] T002 [P] Add `cache-flush` route to the existing `portal-backend` service entry in `services/kong/kong.yml`: path `/cache/flush`, method `DELETE`, `strip_path: false`, with a `key-auth` plugin (`hide_credentials: false`) and a `rate-limiting` plugin (`minute: 10, policy: local`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared helpers needed by both user stories — master key validation, Redis SCAN+DEL, and audit logging. All touch `services/portal-backend/main.py`.

**⚠️ CRITICAL**: No user story implementation can be tested until this phase is complete.

- [x] T003 Add `import asyncio, hashlib, json, logging, os` to the stdlib imports and `import redis.asyncio as aioredis` to the third-party imports in `services/portal-backend/main.py`; add module-level `REDIS_URL: str = os.environ.get("REDIS_URL", "redis://redis:6379")` and `LITELLM_MASTER_KEY: str = os.environ.get("LITELLM_MASTER_KEY", "")`; add `audit = logging.getLogger("portal-backend.audit")`
- [x] T004 Add `def _validate_master_key(authorization: str) -> None` to `services/portal-backend/main.py`: if `authorization` is empty/missing raise `HTTPException(401, _build_error("unauthorized", "Master key required."))`, else if `authorization.removeprefix("Bearer ") != LITELLM_MASTER_KEY` raise `HTTPException(403, _build_error("forbidden", "Only the platform master key may flush the cache."))`
- [x] T005 Add `async def _redis_scan_del(pattern: str) -> int` to `services/portal-backend/main.py`: connect to `REDIS_URL` using `aioredis.from_url`, iterate `client.scan_iter(pattern, count=100)` collecting keys in batches of 100, delete each batch with `client.delete(*batch)`; return total deleted count; wrap in `try/except redis.exceptions.RedisError` and raise `HTTPException(503, _build_error("cache_unavailable", "Cache backend unreachable."))` on failure
- [x] T006 Add `def _write_cache_audit(request_id: str, key_hash: str, scope: str, model: str, keys_deleted: int, status_code: int) -> None` to `services/portal-backend/main.py`: writes `audit.info(json.dumps({...}))` with fields `timestamp`, `event_type` (`cache_flush_all` or `cache_flush_model`), `request_id`, `key_hash`, `model`, `keys_deleted`, `status_code` — no raw key value, no cache key names
- [x] T007 Add Pydantic response model `class CacheFlushResult(BaseModel)` with fields `keys_deleted: int` and `model: str | None = None` to `services/portal-backend/main.py`

**Checkpoint**: `make up-core` → `make up-portal` — portal-backend starts cleanly with Redis client importable; Kong route registered.

---

## Phase 3: User Story 1 — Full Cache Flush (Priority: P1) 🎯 MVP

**Goal**: `DELETE /cache/flush` removes all `llm_cache:*` Redis entries when called with the master key, and returns `{"keys_deleted": <int>}`.

**Independent Test**: `curl -X DELETE http://localhost:8080/cache/flush -H "Authorization: Bearer $SMOKE_API_KEY"` → HTTP 200, `{"keys_deleted": <integer ≥ 0>}`.

- [x] T008 [US1] Implement `DELETE /cache/flush` endpoint in `services/portal-backend/main.py`: extract `Authorization` header via `Header(..., alias="Authorization")`; call `_validate_master_key(authorization)`; call `count = await _redis_scan_del("llm_cache:*")`; call `_write_cache_audit(request_id=request.headers.get("x-request-id",""), key_hash=hashlib.sha256(authorization.encode()).hexdigest(), scope="all", model="all", keys_deleted=count, status_code=200)`; return `CacheFlushResult(keys_deleted=count)`
- [x] T009 [US1] Write contract tests `test_full_flush_success`, `test_full_flush_no_key_returns_401`, `test_full_flush_non_master_returns_403`, and `test_full_flush_empty_cache_returns_zero` in `tests/contract/test_cache_flush.py` — use `httpx.AsyncClient(app=app, base_url="http://test")` with `pytest.mark.asyncio`; mock `_redis_scan_del` and `_write_cache_audit` with `pytest.monkeypatch` or `unittest.mock.AsyncMock`

**Checkpoint**: US1 independently testable — `curl` delivers HTTP 200 with `keys_deleted` integer.

---

## Phase 4: User Story 2 — Model-Scoped Cache Flush (Priority: P2)

**Goal**: `DELETE /cache/flush?model={name}` removes only `llm_cache:<model>:*` entries; validates model name against LiteLLM catalogue; returns `{"keys_deleted": <int>, "model": "<name>"}`.

**Independent Test**: `curl -X DELETE "http://localhost:8080/cache/flush?model=gpt-4o-mini" -H "Authorization: Bearer $SMOKE_API_KEY"` → HTTP 200, `{"keys_deleted": <integer ≥ 0>, "model": "gpt-4o-mini"}`. And: `curl -X DELETE "http://localhost:8080/cache/flush?model=bad-model"` → HTTP 422 with `valid_models` list.

- [x] T010 [US2] Add `async def _get_model_names(authorization: str) -> list[str]` to `services/portal-backend/main.py`: call `GET http://litellm:4000/v1/models` with the `Authorization` header using the existing `httpx.AsyncClient` pattern; extract `[m["id"] for m in response.json().get("data", [])]`; on `httpx.RequestError` raise `HTTPException(503, _build_error("model_catalogue_unavailable", "Cannot validate model name — LiteLLM unreachable."))`
- [x] T011 [US2] Implement `DELETE /cache/flush` endpoint update in `services/portal-backend/main.py` to accept an optional `model: str | None = Query(default=None)` parameter: if `model` is not `None`, call `_validate_master_key`, then `valid = await _get_model_names(authorization)`, then if `model not in valid` raise `HTTPException(422, _build_error("invalid_model", "Unknown model name.", {"valid_models": valid}))`; call `count = await _redis_scan_del(f"llm_cache:{model}:*")`; call `_write_cache_audit(..., scope="model", model=model, ...)`; return `CacheFlushResult(keys_deleted=count, model=model)` — if `model` is `None` fall through to the existing full-flush logic from T008
- [x] T012 [US2] Write contract tests `test_scoped_flush_success`, `test_scoped_flush_invalid_model_returns_422_with_valid_list`, and `test_scoped_flush_non_master_returns_403` in `tests/contract/test_cache_flush.py`; mock `_get_model_names` to return a fixed list; assert `422` response body contains `detail.valid_models`

**Checkpoint**: US1 and US2 independently testable — scoped flush returns model echo; invalid model returns 422 with catalogue.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Smoke test automation and code quality gates.

- [x] T013 [P] Create `tests/smoke/test_cache_flush.sh`: 6 non-interactive `curl` tests matching Tests 1–6 from `specs/022-cache-flush-management/quickstart.md` — full flush (HTTP 200 + `keys_deleted`), scoped flush (HTTP 200 + `model` echo), no-key rejection (HTTP 401), non-master rejection (HTTP 403), invalid model (HTTP 422 + `valid_models`), idempotency (HTTP 200 + `keys_deleted: 0`); skip gracefully if `SMOKE_API_KEY` unset
- [x] T014 [P] Run `ruff check services/portal-backend/main.py` — zero warnings
- [x] T015 [P] Run `mypy services/portal-backend/main.py --ignore-missing-imports` — zero type errors; all new function signatures have complete type annotations
- [ ] T016 Validate acceptance criteria SC-001–SC-006 using the `curl` commands in `specs/022-cache-flush-management/quickstart.md` with a live stack (`make up-core && make up-portal && make seed-kong`)
- [ ] T017 Run `make smoke` to confirm the full platform smoke test still passes after all changes

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately. T001 and T002 are parallel.
- **Foundational (Phase 2)**: Depends on Phase 1 completion. **Blocks both user story phases.**
- **US1 (Phase 3)**: Depends on Phase 2. No dependency on US2.
- **US2 (Phase 4)**: Depends on Phase 2 and Phase 3 (T011 extends the endpoint from T008).
- **Polish (Phase 5)**: Depends on all prior phases. T013–T015 are parallel.

### User Story Dependencies

| Story | Can start after | Depends on other stories? |
|---|---|---|
| US1 (P1) | Phase 2 complete | None |
| US2 (P2) | Phase 3 complete | T011 extends the T008 endpoint — US2 adds `?model=` branch to same function |

### Within Each User Story

- Foundational helpers (T003–T007) before any endpoint code
- Endpoint implementation before contract tests
- Both user stories complete before smoke test (T013)

---

## Parallel Opportunities

### Phase 1 Parallel

```
T001 (requirements.txt)   ←── independent
T002 (kong.yml)           ←── independent
```

### Phase 2 Sequential

T003 → T004 → T005 → T006 → T007 all touch `main.py` — run sequentially.

### Phase 5 Parallel

```
T013 (smoke test script)  ←── independent
T014 (ruff)               ←── independent
T015 (mypy)               ←── independent
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001–T002)
2. Complete Phase 2: Foundational (T003–T007) — **CRITICAL**
3. Complete Phase 3: US1 (T008–T009)
4. **STOP AND VALIDATE**: `curl -X DELETE http://localhost:8080/cache/flush -H "Authorization: Bearer $SMOKE_API_KEY"` → HTTP 200
5. Ship MVP — operators can immediately flush stale cache after any global change

### Incremental Delivery

1. Setup + Foundational → Kong route live, helpers wired
2. US1 → full flush works end-to-end (MVP)
3. US2 → model-scoped flush with catalogue validation
4. Polish → code quality gates + automated smoke tests

---

## Notes

- `[P]` tasks touch different files or concerns — safe to run concurrently
- T011 extends the T008 endpoint by adding the `?model=` optional parameter — it is not a separate endpoint but a conditional branch within the same handler
- `hide_credentials: false` on the Kong route is intentional — portal-backend needs the raw `Authorization` value to compare against `LITELLM_MASTER_KEY`; the key is never forwarded to Redis or logged in plaintext
- `redis.asyncio.scan_iter` is non-blocking and cursor-based; it will not lock Redis even on a 100,000-key cache
- The `LITELLM_MASTER_KEY` env var is already present in the platform `.env` — no new secret provisioning needed
- Commit after each phase checkpoint for easy rollback
