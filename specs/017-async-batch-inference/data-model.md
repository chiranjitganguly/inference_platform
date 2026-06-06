# Data Model: Async Batch Inference API (017)

*Phase 1 output. All entities derived from spec.md §Requirements > Key Entities and clarifications.*

---

## Entities

### 1. BatchJob

Represents one caller submission. One row per POST /v1/batch/jobs call.

```sql
CREATE TABLE batch_jobs (
    job_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    consumer_id      TEXT        NOT NULL,                          -- from X-Consumer-Custom-ID (Kong)
    model            TEXT        NOT NULL,                          -- LiteLLM model name
    status           TEXT        NOT NULL DEFAULT 'queued'          -- queued | running | completed
                     CHECK (status IN ('queued', 'running', 'completed')),
    total_items      INT         NOT NULL CHECK (total_items BETWEEN 1 AND 10000),
    completed_items  INT         NOT NULL DEFAULT 0,
    failed_items     INT         NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ,
    results_expires_at TIMESTAMPTZ,                                 -- completed_at + INTERVAL '24 hours'
    results_path     TEXT,                                          -- /results/{job_id}.jsonl
    trace_id         TEXT        NOT NULL DEFAULT ''                -- serialised W3C traceparent for OTel propagation
);

CREATE INDEX idx_batch_jobs_consumer ON batch_jobs (consumer_id);
CREATE INDEX idx_batch_jobs_status   ON batch_jobs (status);
CREATE INDEX idx_batch_jobs_expires  ON batch_jobs (results_expires_at) WHERE results_expires_at IS NOT NULL;
```

**Validation rules**:
- `status` transitions are one-way: `queued → running → completed`. No backward transitions. No `failed` terminal state at job level.
- `completed_items + failed_items` MUST equal `total_items` when `status = completed`.
- `results_expires_at` is set to `completed_at + INTERVAL '24 hours'` at job completion.
- `results_path` is set at job completion to `/results/{job_id}.jsonl`.

**State machine**:
```
           POST /v1/batch/jobs
                  │
                  ▼
               queued  ◄─── initial state
                  │
          (RQ worker starts)
                  │
                  ▼
              running
                  │
     (all items processed)
                  │
                  ▼
             completed  ──► results_expires_at = completed_at + 24h
```

---

### 2. BatchItem

One inference request within a job. N rows per batch_job, one per submitted item.

```sql
CREATE TABLE batch_items (
    id              BIGSERIAL   PRIMARY KEY,
    job_id          UUID        NOT NULL REFERENCES batch_jobs(job_id) ON DELETE CASCADE,
    item_index      INT         NOT NULL,                            -- 0-based, matches submission order
    input_payload   JSONB,                                          -- prompt payload; NULLed after processing (§2.4 exception)
    output_payload  JSONB,                                          -- LiteLLM response; also NULLed after JSONL write
    status          TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'success', 'error')),
    error_detail    TEXT,
    retry_count     INT         NOT NULL DEFAULT 0,
    latency_ms      INT,                                            -- time from LiteLLM call start to response
    processed_at    TIMESTAMPTZ,                                    -- when item reached terminal status
    UNIQUE (job_id, item_index)
);

CREATE INDEX idx_batch_items_job_status ON batch_items (job_id, status);
```

**Validation rules**:
- `item_index` is 0-based and unique within a job.
- `input_payload` is set NULL immediately after `process_item()` completes (success or error).
- `output_payload` is set NULL immediately after the item's line is written to the JSONL file.
- `retry_count` max is 3; after 3 failures the item is recorded as `status: error`.
- `status: error` requires `error_detail` to be non-null.

---

### 3. JSONL Result Record

One line in the `/results/{job_id}.jsonl` file. Ordered by `item_index` ascending.

```json
// Success line
{"index": 0, "status": "success", "output": { /* OpenAI-compatible response */ }, "latency_ms": 1240}

// Error line
{"index": 1, "status": "error", "error_detail": "LiteLLM returned 503: all fallbacks exhausted", "latency_ms": 4021}
```

**Field definitions**:

| Field | Type | Always present | Description |
|---|---|---|---|
| `index` | int | ✅ | 0-based position matching submission order |
| `status` | `"success"` \| `"error"` | ✅ | Terminal status of this item |
| `output` | object | Only when `status=success` | Full LiteLLM response JSON (OpenAI-compatible) |
| `error_detail` | string | Only when `status=error` | Human-readable error message; no prompt text |
| `latency_ms` | int | ✅ | Wall-clock time for LiteLLM call in milliseconds |

**Invariants**:
- Every `item_index` from 0 to N-1 appears exactly once (SC-003).
- File is sorted by `index` ascending regardless of processing order.
- `output` MUST contain the OpenAI-compatible `choices`, `usage`, `model` fields on success.
- `output` MUST NOT contain the original input prompt (only the model response).

---

## Data Flow

```
POST /v1/batch/jobs
  │
  ├── INSERT batch_jobs (status=queued, total_items=N)
  ├── INSERT batch_items[0..N-1] (status=pending, input_payload=<prompt>)
  ├── Create OTel parent span → serialise traceparent → store in batch_jobs.trace_id
  └── enqueue RQ job(job_id) → return 202

RQ Worker picks up job_id
  │
  ├── UPDATE batch_jobs SET status=running, started_at=now()
  ├── Deserialise traceparent from batch_jobs.trace_id (OTel context propagation)
  ├── asyncio.Semaphore(MAX_CONCURRENT) controls parallel item processing
  │
  ├── For each item (concurrent, bounded by semaphore):
  │   ├── Create child OTel span (parent=job span)
  │   ├── POST http://litellm:4000/v1/chat/completions (with retry ≤3)
  │   ├── UPDATE batch_items SET status=success|error, error_detail, latency_ms, processed_at=now(), input_payload=NULL, output_payload=<response>
  │   ├── Increment batch_jobs.completed_items or failed_items (atomic UPDATE)
  │   ├── Write JSONL line to /results/{job_id}.jsonl (append)
  │   ├── UPDATE batch_items SET output_payload=NULL
  │   └── End child OTel span
  │
  ├── Sort and finalise JSONL file (rewrite sorted by index)
  ├── UPDATE batch_jobs SET status=completed, completed_at=now(), results_expires_at=now()+24h, results_path='/results/{job_id}.jsonl'
  └── End parent OTel span → export to OTel Collector :4318

APScheduler (every 5 minutes):
  └── SELECT job_id, results_path FROM batch_jobs WHERE results_expires_at < now()
      ├── DELETE file at results_path
      └── UPDATE batch_jobs SET results_path=NULL (row retained for 7-day audit window, then hard-deleted)

Safety net cleanup (every 2 hours):
  └── UPDATE batch_items SET input_payload=NULL WHERE processed_at < now() - INTERVAL '2 hours' AND input_payload IS NOT NULL
```

---

## Response Shapes (API Layer)

### POST /v1/batch/jobs — 202 Response
```json
{
  "job_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "queued",
  "total_items": 500,
  "created_at": "2026-06-06T10:00:00Z"
}
```

### GET /v1/batch/jobs/{id} — 200 Response
```json
{
  "job_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "running",
  "model": "gpt-4o",
  "total_items": 500,
  "completed_items": 120,
  "failed_items": 3,
  "created_at": "2026-06-06T10:00:00Z",
  "started_at": "2026-06-06T10:00:01Z",
  "completed_at": null,
  "results_expires_at": null
}
```

### GET /v1/batch/jobs/{id}/results — 200 Response
```
Content-Type: application/x-ndjson
Transfer-Encoding: chunked

{"index": 0, "status": "success", "output": {...}, "latency_ms": 1240}
{"index": 1, "status": "error", "error_detail": "timeout", "latency_ms": 30000}
...
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `BATCH_DATABASE_URL` | — | `postgresql://user:pass@postgres:5432/batch` |
| `LITELLM_BASE_URL` | `http://litellm:4000` | LiteLLM proxy URL |
| `REDIS_QUEUE_URL` | `redis://redis-queue:6380` | RQ broker URL |
| `MAX_CONCURRENT` | `4` | Max parallel LiteLLM calls per job |
| `RESULTS_DIR` | `/results` | Filesystem path for JSONL files |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4318` | OTel Collector OTLP HTTP endpoint |
| `OTEL_SERVICE_NAME` | `batch-worker` | Service name in traces |
