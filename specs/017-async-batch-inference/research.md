# Research: Async Batch Inference API (017)

*Phase 0 output — all NEEDS CLARIFICATION items resolved before Phase 1 design.*

---

## §1 — RQ (Redis Queue) Patterns for Batch Processing

**Decision**: Use RQ with one job per batch submission. The job function manages internal concurrency via `asyncio.Semaphore`.

**Rationale**: The batch model is "one submission = one logical unit of work with N items". A single RQ job represents the entire batch, keeping job-level state (started_at, completed_at) easy to track without cross-job coordination. RQ is synchronous by default, but a job function can run an asyncio event loop internally with `asyncio.run()`, enabling concurrent LiteLLM calls via `asyncio.Semaphore(MAX_CONCURRENT)`.

**How it works**:
1. POST /v1/batch/jobs → FastAPI inserts `batch_jobs` row (status=queued) + `batch_items` rows → enqueues one RQ job with `job_id` as payload → returns 202.
2. RQ worker picks up job → updates status=running → runs `asyncio.run(process_batch(job_id))`.
3. `process_batch` loads items from DB, runs them with `asyncio.gather` bounded by `asyncio.Semaphore(MAX_CONCURRENT)`, writes results to JSONL, purges input payloads, updates status=completed.

**Alternatives considered**:
- Celery: more complex, requires broker + result backend; overkill for this use case.
- Dramatiq: similar complexity to Celery; no existing dependency in the platform.
- RQ Scheduler: not needed; items are processed immediately, not at a future time.

---

## §2 — Concurrency Control: MAX_CONCURRENT with asyncio

**Decision**: `asyncio.Semaphore(int(os.getenv("MAX_CONCURRENT", "4")))` inside the RQ job function controls parallel LiteLLM calls. The semaphore is created once per job and shared across all item coroutines.

**Rationale**: A semaphore is the simplest correct mechanism. Each item coroutine acquires the semaphore before calling LiteLLM and releases it after (whether success or error). At most `MAX_CONCURRENT` calls are in-flight at any moment, regardless of batch size. The semaphore is local to the process — sufficient for a single-worker deployment. For multi-worker scale-out, a Redis-based distributed semaphore would be needed (deferred to Phase 09+).

**Implementation sketch**:
```python
sem = asyncio.Semaphore(MAX_CONCURRENT)

async def process_item(job_id, item, sem, client, tracer, parent_ctx):
    async with sem:
        # create child span, call LiteLLM, write result
        ...

async def process_batch(job_id):
    items = await db.get_pending_items(job_id)
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient(base_url=LITELLM_URL, timeout=120.0) as client:
        await asyncio.gather(*(process_item(job_id, item, sem, client, ...) for item in items))
```

**Alternatives considered**:
- `asyncio.Queue` with worker coroutines: more complex, same semantics for this use case.
- RQ worker concurrency (multiple RQ workers): controls job-level parallelism, not item-level; doesn't cap LiteLLM calls per job.
- Redis-based distributed semaphore (redlock): not needed for single-worker deployment; adds failure surface.

---

## §3 — Guardrails Bypass in Batch Path

**Decision**: Batch items do NOT pass through the Guardrails service (:8088). LiteLLM is called directly from the batch worker.

**Rationale**: The Guardrails service is synchronous and would need to be called once per item. For a 1,000-item batch at MAX_CONCURRENT=4, this adds Guardrails overhead to every item. More critically, Guardrails is designed for the real-time request path and is not currently async-capable. The constitution (§2.1) notes the batch worker as an allowed direct caller of LiteLLM.

**Risk accepted**: PII in batch inputs is not scanned by Presidio before reaching LiteLLM. Mitigation: document that batch consumers are responsible for PII redaction at submission time. Phase 09 (Safety) can add optional per-item Guardrails calls inside the worker as an async pipeline stage.

**Alternatives considered**:
- Call Guardrails per item inside the worker: synchronous, would serialize every item through Guardrails regardless of semaphore, degrading throughput.
- Submit items through Kong→Guardrails→LiteLLM chain: defeats async batch architecture; Kong is an HTTP gateway, not a batch processor.

---

## §4 — OpenTelemetry: Long-Lived Parent Span + Child Item Spans

**Decision**: Create the parent job span at submission time (in FastAPI). Serialise the span context (`traceparent`) to the `batch_jobs.trace_id` column. The RQ worker deserialises the context and creates child spans for each item.

**Rationale**: The OTel spec allows context propagation via any carrier. Storing the W3C `traceparent` string in the DB is the simplest bridge between the HTTP process (FastAPI) and the worker process (RQ). The parent span is kept "open" by not calling `span.end()` at submission — instead the span context is re-attached in the worker and the parent span is ended there. In practice, with OTLP export, spans are only sent on `end()`, so the parent span export happens at job completion.

**Implementation details**:
- `otel.py` initialises a `TracerProvider` with `OTLPSpanExporter(endpoint="http://otel-collector:4318/v1/traces")`.
- Parent span: `tracer.start_span("batch_job", kind=SpanKind.SERVER)` at submission. Serialised via `propagate.inject(carrier)`.
- Worker: `propagate.extract(carrier)` → `tracer.start_as_current_span("batch_item", context=parent_ctx, kind=SpanKind.CLIENT)` per item.
- Required span attributes: `batch_job_id`, `item_index`, `model`, `status`, `latency_ms`.

**Alternatives considered**:
- Pass span context via RQ job args: works but ties OTel to RQ serialisation format.
- Start a new root span in the worker and link to job span via `Link`: supported by OTel spec but loses parent-child hierarchy in Phoenix UI; child-of relationship is what the spec requires.
- Send to Phoenix directly (skipping OTel Collector): spec says export to OTel Collector port 4318, which then forwards to Phoenix. This is consistent with the platform's other services.

---

## §5 — Prompt Storage: Justified Exception to Constitution §2.4

**Decision**: `batch_items.input_payload` (JSONB) stores the submitted prompt payload. This column is NULLED OUT immediately after the item is processed (win or lose), not after job completion.

**Rationale**: Async processing cannot work without persisting the input payload from submission to processing time. The storage window is bounded: a prompt lives in the DB for at most the time the worker takes to reach that item in the queue. For a 10,000-item batch at MAX_CONCURRENT=4 with 2s average item latency, that is at most ~5,000 seconds (~83 minutes) for the last item. After processing, `input_payload` is set to NULL via UPDATE, not DELETE — the row stays for status tracking but the content is gone.

**Controls**:
- `input_payload` column access is restricted to the batch-worker service credentials only.
- `input_payload` is NEVER logged, NEVER included in OTel span attributes, NEVER in Loki entries.
- The `batch_items` table has a `processed_at` timestamp; a daily cleanup job NULLs any `input_payload` rows older than 2 hours as a safety net.

---

## §6 — Results Storage: JSONL on Filesystem

**Decision**: JSONL result files are written to a Docker volume at `/results/{job_id}.jsonl`. The file path is stored in `batch_jobs.results_path`. Auto-deletion runs via APScheduler 24h after `batch_jobs.completed_at`.

**Rationale**: File-system storage is the simplest approach for JSONL streaming. The `GET /v1/batch/jobs/{id}/results` endpoint reads the file and streams it with `StreamingResponse`. No object storage dependency is introduced (out of scope for this phase; GCS migration is a Phase 10 concern).

**Alternatives considered**:
- Store JSONL in PostgreSQL as BYTEA: large result sets would bloat the `batch` DB; streaming is awkward.
- Store results in Redis: Redis is a queue, not an object store; the `noeviction` policy means results compete with job entries for memory.
- Object storage (GCS/S3): not available in local Docker Compose development environment; deferred to Phase 10.

---

## §7 — Job and Item Isolation (FR-011)

**Decision**: The `consumer_id` field on `batch_jobs` is populated from the Kong-forwarded `X-Consumer-Custom-ID` header. All status and result endpoints validate that the requesting consumer matches `job.consumer_id`.

**Rationale**: Kong adds `X-Consumer-Custom-ID` to every authenticated request via the key-auth plugin. The batch-api reads this header and enforces ownership at the application layer. If it does not match, return 404 (not 403) to avoid leaking job existence to other consumers.

---

## §8 — Item Retry Strategy (FR-006)

**Decision**: Each item is retried up to 3 times on transient errors (5xx from LiteLLM, connection errors, timeouts). Retry count is tracked in `batch_items.retry_count`. Retries use exponential backoff: 1s, 2s, 4s.

**Rationale**: 3 retries with exponential backoff handles transient LiteLLM errors without significantly delaying job completion. Non-transient errors (4xx from LiteLLM) are recorded immediately as `status: error` without retry.

---

## §9 — New `batch` PostgreSQL Database

**Decision**: Create a new 7th database `batch` in PostgreSQL. Update `scripts/init-db.sql` to add the CREATE DATABASE statement.

**Rationale**: Schema isolation. The `litellm` database is managed by LiteLLM's own Alembic migrations; adding batch tables there risks conflicts during LiteLLM upgrades. A separate database gives the batch service full DDL control and independent backup/restore.
