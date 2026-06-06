# Implementation Plan: Async Batch Inference API

**Branch**: `017-async-batch-inference` | **Date**: 2026-06-06 | **Spec**: `specs/017-async-batch-inference/spec.md`

**Input**: FastAPI batch-worker on port 8091. RQ (Redis Queue) backed by redis-queue on port 6380. POST /v1/batch/jobs returns 202 with job_id. GET /v1/batch/jobs/{id} returns status. GET /v1/batch/jobs/{id}/results returns JSONL. Worker calls LiteLLM at http://litellm:4000. MAX_CONCURRENT=4. OpenTelemetry Python SDK in batch-worker: job span as parent, item spans as children exported to OTel Collector port 4318.

---

## Summary

Build an async batch inference API where callers submit up to 10,000 inference items in one request, receive a `job_id` immediately (202), and later poll for status and download JSONL results. The system uses RQ (backed by the existing redis-queue on port 6380) for job queuing, FastAPI on port 8091 for HTTP endpoints, PostgreSQL (`batch` database) for job/item state, and asyncio-based internal concurrency capped by `MAX_CONCURRENT=4` to protect LiteLLM. Each job emits one parent OTel span and one child span per item, exported to the OTel Collector at port 4318.

---

## Technical Context

**Language/Version**: Python 3.12 (matches portal-backend and guardrails)

**Primary Dependencies**:
- `fastapi==0.115.0` — HTTP API layer
- `uvicorn[standard]==0.32.0` — ASGI server
- `rq==1.16.2` — job queue (Redis Queue)
- `asyncpg==0.29.0` — async PostgreSQL driver
- `httpx==0.27.2` — async HTTP client to LiteLLM
- `opentelemetry-sdk==1.24.0` — tracing SDK
- `opentelemetry-exporter-otlp-proto-http==1.24.0` — OTLP HTTP export to collector
- `opentelemetry-instrumentation-fastapi==0.45b0` — auto-instrument HTTP endpoints
- `apscheduler==3.10.4` — background cleanup of expired results
- `pydantic==2.x` — request/response validation (FastAPI default)

**Storage**:
- PostgreSQL `batch` database — job and item state (new, see init-db.sql update)
- Local Docker volume at `/results` — JSONL result files (auto-deleted after 24h)
- Redis queue (port 6380, noeviction) — RQ job queue

**Testing**: pytest + httpx test client; smoke tests via `scripts/smoke-test.sh`

**Target Platform**: Linux/amd64 + linux/arm64 Docker container (matches platform multi-arch requirement)

**Project Type**: Web service (FastAPI) + background worker (RQ)

**Performance Goals**:
- POST /v1/batch/jobs returns 202 within 1 second for any valid batch size
- Per-item throughput: bounded by `MAX_CONCURRENT × average_item_latency`

**Constraints**:
- Never more than `MAX_CONCURRENT` (env var, default 4) simultaneous in-flight LiteLLM calls
- Results stored for exactly 24 hours then hard-deleted (FR-010)
- Batch size limit: 1–10,000 items (FR-001, FR-012)
- All endpoints behind Kong :8080 with auth and rate limiting

**Scale/Scope**: Single batch job = up to 10,000 items. MAX_CONCURRENT=4 limits proxy load.

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Justification |
|---|---|---|
| **I — Request Flow Integrity** | ✅ PASS (with note) | Batch-worker calls LiteLLM directly. The constitution (§2.1) explicitly lists the batch worker as an allowed LiteLLM caller alongside the guardrails service. All batch API endpoints are exposed through Kong :8080. The batch worker port 8091 has no host binding (internal only). |
| **II — Prompt Content is Ephemeral** | ⚠️ JUSTIFIED EXCEPTION | Async processing inherently requires storing input payloads until they are consumed by the worker. The exception is bounded: `input_payload` stored in `batch_items.input_payload` (JSONB) is purged from the database immediately after the item is processed (whether success or error). The resulting `output_payload` is written to the JSONL file on disk — not to any PostgreSQL column — and auto-deleted 24h after job completion. Prompts and responses MUST NOT appear in Loki logs, Phoenix span attributes, or any Grafana panel. This exception is documented in Complexity Tracking below. |
| **III — OpenAI Compatibility** | ✅ PASS | Batch endpoints `/v1/batch/jobs` are additive extensions under `/v1` and do not break existing `/v1/chat/completions`. No change to stable `/v1` inference surface. |
| **IV — Defence in Depth** | ✅ PASS | Kong routes enforce auth and rate limiting before reaching batch-api:8091. OPA policy covers batch submission (model check). Guardrails is NOT in the batch path (see research.md §3 for rationale and risk acceptance). |
| **V — Falsifiable Acceptance Criteria** | ✅ PASS | All acceptance criteria are curl-verifiable with expected HTTP status codes and JSON field checks. See spec.md User Stories 1–6. |

---

## Project Structure

### Documentation (this feature)

```text
specs/017-async-batch-inference/
├── plan.md              # This file
├── research.md          # Phase 0: RQ patterns, OTel, concurrency, storage decisions
├── data-model.md        # Phase 1: batch_jobs + batch_items schema, state machine, JSONL format
├── contracts/
│   └── batch-api.md     # HTTP contract: all three endpoints, request/response shapes
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code

```text
services/batch-worker/
├── Dockerfile           # Multi-stage, non-root, multi-platform
├── requirements.txt     # Dependencies listed above
├── main.py              # FastAPI app: POST /v1/batch/jobs, GET /v1/batch/jobs/{id}, GET /v1/batch/jobs/{id}/results
├── worker.py            # RQ job function: processes one batch job using asyncio + semaphore
├── db.py                # asyncpg connection pool + DDL helpers
├── otel.py              # OTel tracer setup, parent/child span helpers
└── cleanup.py           # APScheduler task: deletes expired JSONL files + purges DB rows

scripts/
└── init-db.sql          # Add: CREATE DATABASE batch (update existing file)

services/kong/
└── kong.yml             # Add: batch-api service + 3 routes (POST jobs, GET status, GET results)

docker-compose.yml       # Add: batch-api + batch-worker services under [core] profile

.env.example             # Add: BATCH_DATABASE_URL, OTEL_EXPORTER_OTLP_ENDPOINT (already present?), MAX_CONCURRENT

tests/smoke/
└── smoke-test.sh        # Add batch endpoint checks (update existing file)
```

**Structure Decision**: Two containers from one image — `batch-api` (uvicorn HTTP on 8091) and `batch-worker` (rq worker). Both built from `services/batch-worker/Dockerfile`; differentiated by `command:` override in docker-compose.yml. Shared code in `main.py`, `worker.py`, `db.py`, `otel.py`.

---

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| Prompt content in `batch_items.input_payload` (PostgreSQL) — deviates from Constitution §2.4 | Async processing cannot work without storing the payload for the worker to read. The payload must survive the HTTP request that submitted it. | In-memory storage: lost on worker restart. Redis-only: prompts in Redis values also violates §2.4. Pure RQ payload (serialised in Redis job): still stores prompts in Redis, same violation, plus no SQL query capability for status tracking. The DB approach is the least bad: a single bounded location, column-level access controls possible, and the payload is purged immediately after the item completes. |
| New `batch` PostgreSQL database (7th DB) | Schema isolation keeps batch job/item tables out of the `litellm` DB, which is managed by LiteLLM's own migrations and ORM. Mixing batch DDL into `litellm` DB risks conflicts during LiteLLM upgrades. | Re-use `litellm` DB: risks schema collision with future LiteLLM migrations. |

---

## Post-Phase-1 Constitution Re-check

After contracts and data model were defined:

- **Principle II (re-check)**: The data model (data-model.md) enforces `input_payload` purge at item completion via `UPDATE batch_items SET input_payload = NULL WHERE job_id = $1 AND item_index = $2` immediately after the LiteLLM call returns. The JSONL result file contains only `index`, `status`, `output`, `error_detail` — no input prompt. Phoenix spans contain only `batch_job_id`, `item_index`, `model`, `status`, `latency_ms` — no prompt text. Loki logs contain only structured metadata fields. This satisfies the exception boundary documented above.

- **Principle I (re-check)**: Kong routes (contracts/batch-api.md) point `batch-api:8091` as the upstream. The `batch-api` container exposes no host port. All client traffic enters via Kong :8080. ✅

- **Principle IV (re-check)**: Kong routes use the existing `key-auth` plugin (consumer authentication) and the existing rate-limiting plugin. OPA is wired via Kong pre-request plugin — the existing opa-authz plugin applies to all routes including the batch routes. Guardrails is deliberately bypassed: batch inputs cannot pass through the synchronous Guardrails pipeline because the submission endpoint must return 202 within 1 second. **Risk accepted**: PII in batch inputs is a known gap; documented for Phase 09 (Safety) where async Guardrails invocation per item can be added inside the worker.
