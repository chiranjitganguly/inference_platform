# HTTP Contract: Async Batch Inference API (017)

*Phase 1 output. Three endpoints exposed by batch-api:8091, routed through Kong :8080.*

All endpoints:
- Require `Authorization: Bearer <api-key>` (Kong key-auth)
- Return structured errors: `{"error": "...", "message": "...", "detail": {...}}`
- Are prefixed `/v1/batch` — additive under the existing `/v1` stable surface

---

## Endpoint 1: Submit a Batch Job

### `POST /v1/batch/jobs`

**Kong upstream**: `batch-api:8091`
**Kong route**: path prefix `/v1/batch/jobs`, method POST

#### Request

```
POST /v1/batch/jobs HTTP/1.1
Host: localhost:8080
Authorization: Bearer sk-...
Content-Type: application/json
```

```json
{
  "model": "gpt-4o",
  "items": [
    {
      "index": 0,
      "messages": [
        {"role": "user", "content": "Summarise: ..."}
      ]
    },
    {
      "index": 1,
      "messages": [
        {"role": "user", "content": "Translate: ..."}
      ]
    }
  ]
}
```

**Request fields**:

| Field | Type | Required | Constraints |
|---|---|---|---|
| `model` | string | ✅ | Must be a valid LiteLLM catalogue model name |
| `items` | array | ✅ | Length 1–10,000 |
| `items[].index` | int | ✅ | Must be 0-based, unique within the array, contiguous 0..N-1 |
| `items[].messages` | array | ✅ | OpenAI messages format; at least one message |

#### Responses

**202 Accepted** — job accepted and queued

```json
{
  "job_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "queued",
  "total_items": 2,
  "created_at": "2026-06-06T10:00:00Z"
}
```

**400 Bad Request** — validation failure

```json
{"error": "invalid_request", "message": "Batch size exceeds limit of 10,000 items", "detail": {"submitted": 15000, "limit": 10000}}
```

```json
{"error": "invalid_request", "message": "items array must not be empty", "detail": {}}
```

```json
{"error": "invalid_request", "message": "item indices must be contiguous 0..N-1", "detail": {"duplicate_index": 3}}
```

**401 Unauthorized** — missing or invalid API key (returned by Kong before reaching batch-api)

```json
{"error": "unauthorized", "message": "No API key provided", "detail": {}}
```

**429 Too Many Requests** — rate limit hit (returned by Kong)

---

## Endpoint 2: Poll Job Status

### `GET /v1/batch/jobs/{job_id}`

**Kong upstream**: `batch-api:8091`
**Kong route**: path prefix `/v1/batch/jobs/`, method GET (captures `{job_id}` path param)

#### Request

```
GET /v1/batch/jobs/3fa85f64-5717-4562-b3fc-2c963f66afa6 HTTP/1.1
Host: localhost:8080
Authorization: Bearer sk-...
```

No request body.

#### Responses

**200 OK** — job found and belongs to requesting consumer

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

When `status = "completed"`:
```json
{
  "job_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "completed",
  "model": "gpt-4o",
  "total_items": 500,
  "completed_items": 490,
  "failed_items": 10,
  "created_at": "2026-06-06T10:00:00Z",
  "started_at": "2026-06-06T10:00:01Z",
  "completed_at": "2026-06-06T10:08:22Z",
  "results_expires_at": "2026-06-07T10:08:22Z"
}
```

**Status field values**:

| Value | Meaning |
|---|---|
| `queued` | Job accepted, worker not yet started |
| `running` | Worker processing items |
| `completed` | All items processed (some may have `status: error`) |

**404 Not Found** — job does not exist OR belongs to a different consumer (do not distinguish between the two to avoid leaking job existence)

```json
{"error": "not_found", "message": "Job not found", "detail": {"job_id": "3fa85f64-..."}}
```

---

## Endpoint 3: Download Results

### `GET /v1/batch/jobs/{job_id}/results`

**Kong upstream**: `batch-api:8091`
**Kong route**: path prefix `/v1/batch/jobs/`, method GET, path suffix `/results`

#### Request

```
GET /v1/batch/jobs/3fa85f64-5717-4562-b3fc-2c963f66afa6/results HTTP/1.1
Host: localhost:8080
Authorization: Bearer sk-...
```

No request body.

#### Responses

**200 OK** — job is `completed`, results available

```
HTTP/1.1 200 OK
Content-Type: application/x-ndjson
Transfer-Encoding: chunked
Content-Disposition: attachment; filename="batch-3fa85f64.jsonl"
```

Body — newline-delimited JSON, one object per line, sorted by `index` ascending:
```
{"index": 0, "status": "success", "output": {"id": "chatcmpl-...", "choices": [...], "usage": {...}, "model": "gpt-4o"}, "latency_ms": 1240}
{"index": 1, "status": "error", "error_detail": "LiteLLM 503: all fallbacks exhausted", "latency_ms": 4021}
{"index": 2, "status": "success", "output": {"id": "chatcmpl-...", "choices": [...], "usage": {...}, "model": "gpt-4o"}, "latency_ms": 980}
```

**404 Not Found** — job does not exist or belongs to different consumer

```json
{"error": "not_found", "message": "Job not found", "detail": {"job_id": "3fa85f64-..."}}
```

**409 Conflict** — job exists but is not yet `completed` (status is `queued` or `running`)

```json
{"error": "job_not_complete", "message": "Job results are not yet available", "detail": {"job_id": "3fa85f64-...", "status": "running", "completed_items": 120, "total_items": 500}}
```

**410 Gone** — job existed but results have been auto-deleted (beyond 24h retention window)

```json
{"error": "results_expired", "message": "Results have expired and been deleted", "detail": {"job_id": "3fa85f64-...", "expired_at": "2026-06-07T10:08:22Z"}}
```

---

## Kong Configuration

Routes and services to add to `services/kong/kong.yml`:

```yaml
services:
  - name: batch-api
    url: http://batch-api:8091
    routes:
      - name: batch-jobs-submit
        paths: ["/v1/batch/jobs"]
        methods: [POST]
        strip_path: false
      - name: batch-jobs-status
        paths: ["~/v1/batch/jobs/[^/]+$"]
        methods: [GET]
        strip_path: false
      - name: batch-jobs-results
        paths: ["~/v1/batch/jobs/[^/]+/results$"]
        methods: [GET]
        strip_path: false
    plugins:
      - name: key-auth           # enforces Authorization header
      - name: rate-limiting
        config:
          minute: 30             # 30 batch submissions per minute per consumer
          policy: local
```

---

## OTel Span Contract

### Parent Span (created at submission, ended at job completion)

| Attribute | Value |
|---|---|
| `span.name` | `batch_job` |
| `span.kind` | `SERVER` |
| `batch_job_id` | UUID string |
| `model` | model name |
| `total_items` | int |
| `span.status` | `OK` on completion |

### Child Span (one per item, created and ended during worker processing)

| Attribute | Value |
|---|---|
| `span.name` | `batch_item` |
| `span.kind` | `CLIENT` |
| `batch_job_id` | UUID string (same as parent) |
| `item_index` | int (0-based) |
| `model` | model name |
| `status` | `"success"` or `"error"` |
| `latency_ms` | int |
| `span.status` | `OK` or `ERROR` |
| `exception.message` | error string (only on ERROR spans) |

**No prompt content in any span attribute.** This is a hard constraint.

---

## Smoke Test Commands

Add to `scripts/smoke-test.sh`:

```bash
# Submit a batch job
JOB=$(curl -sf -X POST http://localhost:8080/v1/batch/jobs \
  -H "Authorization: Bearer $BATCH_TEST_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","items":[{"index":0,"messages":[{"role":"user","content":"Say hello"}]}]}')
echo "Submit: $JOB"
JOB_ID=$(echo $JOB | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")

# Poll status — expect 200 with job_id, status, total_items
curl -sf http://localhost:8080/v1/batch/jobs/$JOB_ID \
  -H "Authorization: Bearer $BATCH_TEST_KEY"

# 404 for unknown job
curl -o /dev/null -w "%{http_code}" http://localhost:8080/v1/batch/jobs/00000000-0000-0000-0000-000000000000 \
  -H "Authorization: Bearer $BATCH_TEST_KEY" | grep -q 404

# 400 for oversized batch
curl -o /dev/null -w "%{http_code}" -X POST http://localhost:8080/v1/batch/jobs \
  -H "Authorization: Bearer $BATCH_TEST_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","items":[]}' | grep -q 400
```
