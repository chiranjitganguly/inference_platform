# Tasks: Async Batch Inference API (017)

**Input**: Design documents from `specs/017-async-batch-inference/`

**Prerequisites**: plan.md ✅ spec.md ✅ research.md ✅ data-model.md ✅ contracts/batch-api.md ✅

**Tests**: No test tasks generated — not requested in spec.md.

**Organization**: Tasks grouped by user story (US1→US6) to enable independent implementation and verification of each story.

---

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no shared state)
- **[Story]**: User story this task belongs to (US1–US6)
- No Story label = Setup or Foundational

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the batch-worker service scaffold so all subsequent phases have a build target.

- [x] T001 Create `services/batch-worker/` directory with multi-stage `Dockerfile` — builder stage installs deps, runtime stage uses `python:3.12-slim`, adds non-root user `appuser`, `EXPOSE 8091`, default `CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8091"]`
- [x] T002 Create `services/batch-worker/requirements.txt` with exact pinned versions: `fastapi==0.115.0`, `uvicorn[standard]==0.32.0`, `rq==1.16.2`, `asyncpg==0.29.0`, `httpx==0.27.2`, `opentelemetry-sdk==1.24.0`, `opentelemetry-exporter-otlp-proto-http==1.24.0`, `opentelemetry-instrumentation-fastapi==0.45b0`, `apscheduler==3.10.4`, `redis==5.0.3`, `pydantic==2.7.0`
- [x] T003 [P] Add new env vars to `.env.example`: `BATCH_DATABASE_URL`, `REDIS_QUEUE_URL`, `MAX_CONCURRENT`, `RESULTS_DIR`, `OTEL_SERVICE_NAME` (one per line, no values, comments explaining each)
- [x] T004 [P] Add 7th database to `scripts/init-db.sql` — `SELECT format('CREATE DATABASE %I', 'batch') WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'batch')` + `GRANT ALL PRIVILEGES ON DATABASE batch TO CURRENT_USER`

**Checkpoint**: `docker compose build batch-api` succeeds; `python -c "import fastapi, rq, asyncpg, httpx"` passes inside container.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared infrastructure that MUST be complete before any user story endpoint can work.

⚠️ **CRITICAL**: All user story tasks depend on this phase.

- [x] T005 Create `services/batch-worker/db.py` — asyncpg connection pool singleton (`_pool: asyncpg.Pool | None`), `async def init_pool(dsn: str) -> None`, `def get_pool() -> asyncpg.Pool`, `async def create_schema() -> None` running `CREATE TABLE IF NOT EXISTS batch_jobs (...)` and `CREATE TABLE IF NOT EXISTS batch_items (...)` with full DDL from `data-model.md` (indexes included), CRUD functions: `insert_job(job_id, consumer_id, model, total_items, trace_id) -> None`, `insert_items(job_id, items: list[dict]) -> None`, `get_job(job_id) -> asyncpg.Record | None`, `update_job_status(job_id, status, **kwargs) -> None`, `update_item_status(job_id, index, status, error_detail, latency_ms) -> None`, `null_item_payload(job_id, index) -> None`, `update_job_counters(job_id, success_delta: int, fail_delta: int) -> None`
- [x] T006 [P] Add `batch-api` and `batch-worker` services to `docker-compose.yml` under `profiles: [core]` — `batch-api`: `build: context: ./services/batch-worker`, `expose: ["8091"]`, `ports: ["8091:8091"]` (dev only), `environment: BATCH_DATABASE_URL REDIS_QUEUE_URL MAX_CONCURRENT RESULTS_DIR LITELLM_BASE_URL OTEL_EXPORTER_OTLP_ENDPOINT OTEL_SERVICE_NAME`, `volumes: [batch_results:/results]`, `depends_on: postgres redis-queue`, `healthcheck: curl -sf http://localhost:8091/health`; `batch-worker`: same image/build, `command: ["rq", "worker", "batch", "--url", "${REDIS_QUEUE_URL}"]`, no ports, same environment + volume, depends_on batch-api (waits for schema creation); add named volume `batch_results:` to top-level `volumes:`
- [x] T007 [P] Add batch-api upstream and 3 routes to `services/kong/kong.yml` — service `batch-api` with `url: http://batch-api:8091`; route `batch-submit` (POST `/v1/batch/jobs`); route `batch-results` (GET regex `~/v1/batch/jobs/[^/]+/results$`, must be declared before status route); route `batch-status` (GET regex `~/v1/batch/jobs/[^/]+$`); attach `key-auth` plugin and `rate-limiting` plugin (`minute: 30, policy: local`) to all three routes via `plugins:` on the service
- [x] T008 Create `services/batch-worker/main.py` — FastAPI app `app = FastAPI(title="batch-worker", version="1.0.0")` with `@asynccontextmanager` lifespan: call `db.init_pool(BATCH_DATABASE_URL)` → `db.create_schema()` → start APScheduler (placeholder, wired in T022); `/health` endpoint returning `{"status": "ok"}`; `_build_error(error, message, detail)` helper; `_get_consumer_id(x_consumer_custom_id: str = Header(...))` dependency; global `HTTPException` handler returning structured JSON

**Checkpoint**: `make up-core` starts; `curl -sf http://localhost:8091/health` returns `{"status":"ok"}`; Kong routes are registered (`curl http://localhost:8001/routes | jq '.data[].name'` shows `batch-submit`, `batch-status`, `batch-results`).

---

## Phase 3: User Story 1 — Submit Batch Job (Priority: P1) 🎯 MVP

**Goal**: Caller POSTs a batch and receives 202 with a unique `job_id` within 1 second, regardless of batch size.

**Independent Test**:
```bash
time curl -s -X POST http://localhost:8080/v1/batch/jobs \
  -H "Authorization: Bearer $BATCH_TEST_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","items":[{"index":0,"messages":[{"role":"user","content":"hello"}]}]}' \
  | jq '{job_id, status, total_items}'
# Expect: {"job_id": "<uuid>", "status": "queued", "total_items": 1} within 1s real time
```

- [x] T009 [US1] Add Pydantic models to `services/batch-worker/main.py`: `BatchItemInput(index: int, messages: list[dict[str, str]])`, `BatchSubmitRequest(model: str, items: list[BatchItemInput])` with `@field_validator("items")` enforcing 1≤len≤10000 and contiguous 0..N-1 indices (raise `ValueError` with clear message on violation), `JobCreatedResponse(job_id: str, status: str, total_items: int, created_at: str)`
- [x] T010 [US1] Implement `POST /v1/batch/jobs` in `services/batch-worker/main.py` — generate `job_id = str(uuid.uuid4())`, call `db.insert_job(job_id, consumer_id, request.model, len(items), trace_id="")`, call `db.insert_items(job_id, [{"item_index": i.index, "input_payload": {"messages": i.messages}} for i in request.items])`, connect to Redis via `redis.from_url(REDIS_QUEUE_URL)`, enqueue `process_batch` via `rq.Queue("batch", connection=redis_conn).enqueue(process_batch, job_id)`, return `JSONResponse(status_code=202, content=JobCreatedResponse(...).model_dump())`; 400 responses for validation errors
- [x] T011 [US1] Create `services/batch-worker/worker.py` — `process_batch(job_id: str) -> None` stub: create asyncpg pool for `BATCH_DATABASE_URL`, call `asyncio.run(_process(job_id, pool))`, close pool; `async def _process(job_id, pool)`: update `batch_jobs.status=running, started_at=now()`; loop through pending items marking each `status=success, latency_ms=0, processed_at=now()`; null `input_payload`; write minimal JSONL line `{"index": i, "status": "success", "output": {}, "latency_ms": 0}`; update `batch_jobs.status=completed, completed_at=now(), results_expires_at=now()+24h, results_path=f"/results/{job_id}.jsonl", completed_items=total_items`

**Checkpoint**: `curl -s -X POST http://localhost:8080/v1/batch/jobs ...` returns HTTP 202 with `job_id` and `status: queued` within 1 second; submitting twice returns two distinct UUIDs; submitting 10001 items returns 400.

---

## Phase 4: User Story 2 — Poll Job Status (Priority: P1)

**Goal**: Caller polls status endpoint and observes `queued → running → completed` with accurate item counts.

**Independent Test**:
```bash
JOB_ID=$(curl -s -X POST http://localhost:8080/v1/batch/jobs \
  -H "Authorization: Bearer $BATCH_TEST_KEY" -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","items":[{"index":0,"messages":[{"role":"user","content":"hi"}]}]}' \
  | jq -r '.job_id')
curl -s http://localhost:8080/v1/batch/jobs/$JOB_ID \
  -H "Authorization: Bearer $BATCH_TEST_KEY" | jq '{status,total_items,completed_items,failed_items}'
# Expect status field present, total_items=1
curl -o /dev/null -w "%{http_code}" \
  http://localhost:8080/v1/batch/jobs/00000000-0000-0000-0000-000000000000 \
  -H "Authorization: Bearer $BATCH_TEST_KEY"
# Expect: 404
```

- [x] T012 [US2] Add `JobStatusResponse` Pydantic model to `services/batch-worker/main.py`: fields `job_id: str`, `status: str`, `model: str`, `total_items: int`, `completed_items: int`, `failed_items: int`, `created_at: str`, `started_at: str | None`, `completed_at: str | None`, `results_expires_at: str | None` — all timestamp fields formatted as ISO-8601 UTC strings via `model_validator`
- [x] T013 [US2] Implement `GET /v1/batch/jobs/{job_id}` in `services/batch-worker/main.py` — call `db.get_job(job_id)`, if `None` raise `HTTPException(404)`; if `row["consumer_id"] != consumer_id` raise `HTTPException(404)` (do not distinguish to avoid leaking existence); map row to `JobStatusResponse` and return 200
- [x] T014 [US2] Update `services/batch-worker/worker.py` `_process()` to set `started_at=now()` when transitioning to `running` and `completed_at=now()` + `results_expires_at=now()+timedelta(hours=24)` when transitioning to `completed` — update `db.update_job_status()` call signatures in `db.py` to accept these kwargs

**Checkpoint**: Poll `GET /v1/batch/jobs/{id}` after submit; observe `status: queued` initially, then `running`, then `completed` with `completed_items=total_items`; unknown job_id returns 404; job from different consumer returns 404.

---

## Phase 5: User Story 3 — Download JSONL Results (Priority: P1)

**Goal**: Completed job serves a valid JSONL file with one line per item in index order.

**Independent Test**:
```bash
# Wait for completed status, then:
curl -s http://localhost:8080/v1/batch/jobs/$JOB_ID/results \
  -H "Authorization: Bearer $BATCH_TEST_KEY" | wc -l
# Expect: N lines matching total_items
curl -s http://localhost:8080/v1/batch/jobs/$JOB_ID/results \
  -H "Authorization: Bearer $BATCH_TEST_KEY" | head -1 | jq '{index,status}'
# Expect: {"index": 0, "status": "success"}
```

- [x] T015 [US3] Replace stub worker body in `services/batch-worker/worker.py` with full async implementation — `_process(job_id, pool)`: load all pending items via `SELECT item_index, input_payload FROM batch_items WHERE job_id=$1 AND status='pending' ORDER BY item_index`; create `results: list[dict] = []` and `asyncio.Semaphore(int(os.getenv("MAX_CONCURRENT","4")))`; create shared `httpx.AsyncClient(base_url=LITELLM_BASE_URL, timeout=120.0)` as async context manager; call `await asyncio.gather(*(process_item(job_id, item, sem, client, results, pool) for item in items))`
- [x] T016 [US3] Implement `async def process_item(job_id, item, sem, client, results, pool)` in `services/batch-worker/worker.py` — `async with sem:`: record `t0 = time.monotonic()`; POST to `/v1/chat/completions` with `{"model": job_model, "messages": item["input_payload"]["messages"]}`; compute `latency_ms = int((time.monotonic()-t0)*1000)`; on success (2xx): `output = resp.json()`; call `db.update_item_status(job_id, item["item_index"], "success", None, latency_ms)`; call `db.update_job_counters(job_id, 1, 0)`; append `{"index": item["item_index"], "status": "success", "output": output, "latency_ms": latency_ms}` to results; on any exception or non-2xx: record as error (see T019 for retry, stub here as single-attempt); call `db.null_item_payload(job_id, item["item_index"])` in `finally`
- [x] T017 [US3] Implement JSONL file writing in `services/batch-worker/worker.py` `_process()` after `asyncio.gather` completes — sort `results` list by `results[i]["index"]`; write each dict as `json.dumps(r) + "\n"` to `/results/{job_id}.jsonl` using `pathlib.Path(RESULTS_DIR) / f"{job_id}.jsonl"` (create RESULTS_DIR if missing); then call `db.update_job_status(job_id, "completed", completed_at=now, results_expires_at=now+24h, results_path=str(path))`
- [x] T018 [US3] Implement `GET /v1/batch/jobs/{job_id}/results` in `services/batch-worker/main.py` — fetch job via `db.get_job(job_id)`; 404 if not found or consumer mismatch; 409 if `status != "completed"` with body `{"error":"job_not_complete","message":"Job results are not yet available","detail":{"status":...,"completed_items":...,"total_items":...}}`; 410 if `results_expires_at < now()` with body `{"error":"results_expired",...}`; return `FileResponse(job["results_path"], media_type="application/x-ndjson", headers={"Content-Disposition": f'attachment; filename="batch-{job_id[:8]}.jsonl"'})`

**Checkpoint**: Submit 5-item batch → wait for `status: completed` → `GET /v1/batch/jobs/{id}/results` returns 200 with exactly 5 JSONL lines, each parseable as JSON with `index`, `status`, `latency_ms` fields; GET results for running job returns 409; GET results for unknown job returns 404.

---

## Phase 6: User Story 4 — Individual Item Failure Handling (Priority: P2)

**Goal**: Items that fail do not stop the job; they appear in JSONL with `status: error`; job reaches `completed`.

**Independent Test**:
```bash
# Submit a batch where at least one item has a bad model or malformed messages
# After completed: verify JSONL has status:error lines and status:success lines
# Verify job status is "completed" (not "failed"), failed_items > 0
curl -s http://localhost:8080/v1/batch/jobs/$JOB_ID \
  -H "Authorization: Bearer $BATCH_TEST_KEY" | jq '{status, failed_items}'
# Expect: {"status": "completed", "failed_items": <N>}
```

- [x] T019 [US4] Add retry logic to `process_item()` in `services/batch-worker/worker.py` — wrap the httpx call in a `for attempt in range(3)` loop; retry on `httpx.TransientError` or response status >= 500 (transient); no retry on 4xx (permanent error); between retries: `await asyncio.sleep(2**attempt)` (1s, 2s — attempt 0→1→2); after 3 failures record `status=error` with `error_detail=f"failed after 3 attempts: {last_error}"`; on non-retryable error record immediately; `process_item()` MUST NOT raise — catch `Exception` at outermost level, record as error with traceback string in `error_detail`; call `db.update_job_counters(job_id, 0, 1)` for error items
- [x] T020 [US4] Verify `db.update_job_counters()` in `services/batch-worker/db.py` uses atomic SQL — `UPDATE batch_jobs SET completed_items = completed_items + $2, failed_items = failed_items + $3 WHERE job_id = $1` — confirm this is a single statement (no read-modify-write) to be safe under concurrent goroutine writes; add `processed_at = now()` update to `update_item_status()` if not already present

**Checkpoint**: Submit batch with one item using model `"nonexistent-model"` → job `status: completed`; JSONL has `status: error` for that item with non-empty `error_detail`; remaining items show `status: success`; `failed_items + completed_items == total_items`.

---

## Phase 7: User Story 5 — Concurrency Limit (Priority: P2)

**Goal**: At most `MAX_CONCURRENT` LiteLLM calls in-flight at any time during batch processing.

**Independent Test**:
```bash
# Set MAX_CONCURRENT=2 in .env, restart batch-worker
# Submit 20-item batch, tail logs:
make logs svc=batch-worker 2>&1 | grep "litellm_call"
# Expect: at most 2 "litellm_call start" lines between any consecutive "litellm_call end" lines
```

- [x] T021 [US5] Confirm `asyncio.Semaphore(int(os.getenv("MAX_CONCURRENT", "4")))` in `services/batch-worker/worker.py` is created ONCE per job (not per item) in `_process()` and passed into every `process_item()` coroutine; confirm `async with sem:` wraps the httpx request (not the DB update or file write); add a structured log line `logger.info("litellm_call", extra={"job_id": job_id, "item_index": ..., "event": "start|end"})` before and after the httpx call for observability
- [x] T022 [US5] Create `services/batch-worker/cleanup.py` — `async def expire_results(pool) -> None`: `SELECT job_id, results_path FROM batch_jobs WHERE results_expires_at < now() AND results_path IS NOT NULL`; for each row `pathlib.Path(row["results_path"]).unlink(missing_ok=True)`; `UPDATE batch_jobs SET results_path=NULL WHERE job_id=$1`; `async def safety_net_cleanup(pool) -> None`: `UPDATE batch_items SET input_payload=NULL WHERE processed_at < now() - INTERVAL '2 hours' AND input_payload IS NOT NULL`; wire both into `main.py` lifespan via APScheduler `BackgroundScheduler`: `expire_results` every 5 minutes, `safety_net_cleanup` every 2 hours — use `asyncio.run_coroutine_threadsafe` or make cleanup functions sync wrappers calling `asyncio.run()`

**Checkpoint**: `make stats` shows batch-worker memory stable during large batch; logs show ≤MAX_CONCURRENT concurrent `litellm_call start` events; expired job returns 410 after retention window.

---

## Phase 8: User Story 6 — OTel Tracing (Priority: P3)

**Goal**: Phoenix shows 1 parent `batch_job` span and N child `batch_item` spans per completed job.

**Independent Test**:
```bash
# Submit 3-item batch, wait for completed
# Open Phoenix at http://localhost:6006, filter by trace containing batch_job_id
# Expect: 1 parent span (batch_job) + 3 child spans (batch_item) with correct attributes
```

- [x] T023 [US6] Create `services/batch-worker/otel.py` — import `opentelemetry.sdk.trace.TracerProvider`, `opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter`, `opentelemetry.sdk.trace.export.BatchSpanProcessor`, `opentelemetry.propagate.set_global_textmap`, `opentelemetry.propagators.composite.CompositePropagator`, `opentelemetry.propagators.b3.B3Format` (or W3C); `def init_tracer() -> opentelemetry.trace.Tracer`: creates `TracerProvider` with `BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces"))`, calls `opentelemetry.trace.set_tracer_provider(provider)`, sets `W3CTraceContextPropagator` as global propagator, returns `provider.get_tracer(OTEL_SERVICE_NAME)`; `def inject_context(span: Span) -> str`: `carrier: dict = {}`; `propagate.inject(carrier, context=trace.set_span_in_context(span))`; `return json.dumps(carrier)`; `def extract_context(carrier_json: str) -> Context`: `return propagate.extract(json.loads(carrier_json))`
- [x] T024 [US6] Add parent span to `POST /v1/batch/jobs` in `services/batch-worker/main.py` — call `otel.init_tracer()` once in lifespan, store as `app.state.tracer`; in endpoint: `with tracer.start_as_current_span("batch_job", kind=SpanKind.SERVER) as span:` set `span.set_attribute("batch_job_id", job_id)`, `span.set_attribute("model", request.model)`, `span.set_attribute("total_items", len(request.items))`; call `otel.inject_context(span)` to get `trace_id` string; pass `trace_id` to `db.insert_job()` so it is stored in `batch_jobs.trace_id`; span ends naturally when `with` block exits (after 202 response is prepared)
- [x] T025 [US6] Add child span per item in `process_item()` in `services/batch-worker/worker.py` — at start of `process_item()`: fetch `batch_jobs.trace_id` (pass as arg from `_process()` which reads it once); call `parent_ctx = otel.extract_context(trace_id_str)`; `with tracer.start_as_current_span("batch_item", context=parent_ctx, kind=SpanKind.CLIENT) as span:` wrap the retry loop; set `span.set_attribute("batch_job_id", job_id)`, `span.set_attribute("item_index", item_index)`, `span.set_attribute("model", model)`, `span.set_attribute("status", "success"|"error")`, `span.set_attribute("latency_ms", latency_ms)`; on error: `span.set_status(StatusCode.ERROR)`, `span.record_exception(exc)` — span ends when `with` block exits; `_process()` reads `trace_id` from a single `SELECT trace_id FROM batch_jobs WHERE job_id=$1` call and passes to all `process_item()` coroutines

**Checkpoint**: `obs` profile running; submit 3-item batch; `curl http://localhost:6006/v1/traces | jq '...'` or Phoenix UI shows trace with 1 root span (`batch_job`) and 3 child spans (`batch_item`); each child span has `batch_job_id`, `item_index`, `model`, `status`, `latency_ms` attributes; failed items have `ERROR` span status.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Smoke test coverage, lint/type checks, memory budget verification.

- [x] T026 Add batch API smoke test block to `scripts/smoke-test.sh` — submit 1-item batch via Kong :8080, assert 202 and `job_id` field present; poll status endpoint, assert 200 and `status` field present; assert 404 for unknown `job_id`; assert 400 for empty `items` array; assert 400 for `items` exceeding 10000 (use 2-item payload with index gap to test index validation)
- [x] T027 [P] Run `ruff check services/batch-worker/ --fix` and `mypy services/batch-worker/ --ignore-missing-imports --strict` — fix all reported errors; all function signatures must have full type annotations; no bare `Any` types; `httpx.AsyncClient` used everywhere (no `requests`)
- [x] T028 [P] Update `docs/progress.md` to mark feature 017 as `✅ Complete`; update active feature reference if next feature is known
- [x] T029 Verify memory budget with `make stats` after `make up-all` — confirm batch-api + batch-worker together add ≤150 MB to total; confirm all-profiles total stays within ~4.16 GB; document observed values in a comment in `services/batch-worker/Dockerfile`

**Checkpoint**: `make smoke` exits 0; `ruff` and `mypy` both exit 0; `make stats` output confirms memory budget; `docs/progress.md` updated.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — T005 requires Dockerfile built; T006/T007/T008 require env vars from T003/T004
- **Phase 3 (US1)**: Requires Phase 2 complete (db.py, main.py skeleton, docker/kong wired)
- **Phase 4 (US2)**: Requires Phase 3 complete (needs batch_jobs rows to query)
- **Phase 5 (US3)**: Requires Phase 4 complete (needs worker running + completed jobs + status endpoint)
- **Phase 6 (US4)**: Requires Phase 5 complete (builds on worker.py from T015/T016)
- **Phase 7 (US5)**: Requires Phase 5 complete (semaphore goes into worker from T015; cleanup from T022 requires schema)
- **Phase 8 (US6)**: Requires Phase 5 complete (wires into submission endpoint + worker from T015/T016)
- **Phase 9 (Polish)**: Requires all desired user story phases complete

### User Story Dependencies

| Story | Priority | Depends On | Can Parallel With |
|---|---|---|---|
| US1 (Submit) | P1 | Phase 2 | — |
| US2 (Poll) | P1 | US1 | — |
| US3 (Download) | P1 | US1 + US2 | — |
| US4 (Error handling) | P2 | US3 | US5, US6 |
| US5 (Concurrency) | P2 | US3 | US4, US6 |
| US6 (OTel tracing) | P3 | US3 | US4, US5 |

### Within Each User Story

- Pydantic models before endpoint implementation
- `db.py` CRUD helpers before callers
- `worker.py` phases build incrementally (stub → full → retry → semaphore → tracing)

### Parallel Opportunities

| Parallel Group | Tasks |
|---|---|
| Phase 1 setup | T003, T004 (after T001, T002) |
| Phase 2 infra | T006, T007, T008 (after T005) |
| Phase 9 polish | T027, T028 |
| After US3: P2+P3 stories | US4 (T019, T020) ‖ US5 (T021, T022) ‖ US6 (T023, T024, T025) |

---

## Parallel Example: US4, US5, US6 (post-US3)

```
# After Phase 5 (US3) is checkpointed:

Parallel track A — US4 Error Handling:
  Task T019: "Add retry logic to process_item() in services/batch-worker/worker.py"
  Task T020: "Verify atomic counter update in services/batch-worker/db.py"

Parallel track B — US5 Concurrency:
  Task T021: "Confirm semaphore scope in services/batch-worker/worker.py"
  Task T022: "Create services/batch-worker/cleanup.py with APScheduler jobs"

Parallel track C — US6 OTel:
  Task T023: "Create services/batch-worker/otel.py with TracerProvider"
  Task T024: "Add parent span to POST /v1/batch/jobs in services/batch-worker/main.py"
  Task T025: "Add child spans to process_item() in services/batch-worker/worker.py"
```

---

## Implementation Strategy

### MVP First (US1 + US2 + US3 = Three P1 Stories)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: US1 → validate 202 response within 1s
4. Complete Phase 4: US2 → validate status polling
5. Complete Phase 5: US3 → validate JSONL download
6. **STOP and VALIDATE**: All P1 stories functional end-to-end
7. Deploy/demo MVP

### Incremental Delivery

1. Setup + Foundational → container builds, healthcheck passes
2. US1 → 202 submission works → deployed
3. US2 → polling works → deployed
4. US3 → full worker + download → P1 complete → deployed (MVP!)
5. US4 + US5 in parallel → error handling + concurrency → P2 complete
6. US6 → observability → P3 complete
7. Polish → smoke test suite green

### Single Developer Sequence

`T001 → T002 → T003+T004 → T005 → T006+T007+T008 → T009 → T010 → T011 → T012 → T013 → T014 → T015 → T016 → T017 → T018 → T019 → T020 → T021 → T022 → T023 → T024 → T025 → T026 → T027+T028 → T029`

---

## Notes

- **[P] tasks** operate on different files with no shared mutable state — safe to run in parallel
- **`input_payload` discipline**: any code path that reads `input_payload` MUST call `null_item_payload()` afterward (never log or span-attribute the value)
- **No prompt content in spans**: T024/T025 set only metadata attributes — never `messages` content
- **Stub worker in T011** is intentionally minimal — replaced in T015; do not optimise the stub
- **Kong regex routes**: `batch-results` route must be declared before `batch-status` in `kong.yml` (Kong matches first registered route)
- **Results volume**: both `batch-api` and `batch-worker` containers mount the same `batch_results` named volume — file written by worker is readable by API
