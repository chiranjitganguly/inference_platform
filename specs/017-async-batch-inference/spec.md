# Feature Specification: Async Batch Inference API

**Feature Branch**: `017-async-batch-inference`

**Created**: 2026-06-04

**Status**: Draft

**Input**: Build an asynchronous batch inference API that accepts thousands of requests in a single submission and processes them in the background. Callers submit a job and receive a job ID immediately, then poll for status and download results as JSONL when done. Individual item failures must not fail the entire job. A concurrency limit must prevent the batch worker overloading the model proxy. Each batch item must emit an OpenTelemetry span in Phoenix linked to its parent job span.

---

## Clarifications

### Session 2026-06-04

- Q: How long are batch results retained before deletion? → A: Exactly 24 hours after job completion, then auto-deleted (hard deletion, not optional).
- Q: How are individual failed items represented in the JSONL output? → A: Each failed item is recorded with `status: error` in its JSONL line alongside an `error_detail` field; no other status value is used for failures.
- Q: What is the environment variable name for the concurrency ceiling, and what does it govern? → A: `MAX_CONCURRENT` — limits the number of parallel in-flight LLM calls from the batch worker to the model proxy.
- Q: Which Redis instance and configuration backs the batch queue? → A: Redis on port 6380, configured with `noeviction` policy — the same instance already designated for batch jobs in the platform.
- Q: What HTTP status is returned when polling an unknown or non-existent `job_id`? → A: `404 Not Found`.
- Q: How does the batch worker emit traces to Phoenix? → A: The batch worker process hosts the OpenTelemetry SDK directly; it creates one parent span per job at submission time and one child span per item during processing, both exported to Phoenix Arize.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Submit a Batch Job and Receive Immediate Acknowledgement (Priority: P1)

A caller submits a list of inference prompts in a single request and immediately receives a unique job ID. The caller does not wait for any inference to complete — the response is instant regardless of how many items are in the batch.

**Why this priority**: This is the core contract of the batch API. Without it, callers cannot decouple submission from processing, which is the entire point of async batch processing.

**Independent Test**: A client POSTs a batch of 500 items and receives a `202 Accepted` response containing a job ID within one second. The job can then be polled independently.

**Acceptance Scenarios**:

1. **Given** an authenticated caller with a valid API key, **When** they POST a batch of 1–10,000 inference items, **Then** the system returns `202 Accepted` with a unique `job_id` and a `status` of `queued` within 1 second.
2. **Given** a batch submission, **When** the caller reuses the same request body with a different call, **Then** each call produces a distinct `job_id`.
3. **Given** a batch of 10,001 or more items, **When** submitted, **Then** the system returns `400 Bad Request` with a clear message stating the per-submission limit.

---

### User Story 2 — Poll for Job Progress and Know When Results Are Ready (Priority: P1)

A caller periodically checks the status of a submitted job using its job ID. The status response tells them how many items are complete, how many have failed, and whether the overall job is still running or has finished.

**Why this priority**: Without status polling, callers cannot know when to download results, making the batch submission useless.

**Independent Test**: After submitting a batch, the caller polls the status endpoint every few seconds and observes `status` transitioning from `queued` → `running` → `completed`. The final response includes accurate counts of succeeded and failed items.

**Acceptance Scenarios**:

1. **Given** a job that was recently submitted, **When** the caller polls the status endpoint, **Then** the response contains `job_id`, `status` (`queued`, `running`, `completed`, or `failed`), `total_items`, `completed_items`, and `failed_items`.
2. **Given** a job in `running` state, **When** polled, **Then** `completed_items + failed_items` is less than `total_items` and increases over time.
3. **Given** a job in `completed` state, **When** polled, **Then** `completed_items + failed_items` equals `total_items` and a `results_url` or download link is present.
4. **Given** an invalid or non-existent `job_id`, **When** polled, **Then** the system returns `404 Not Found`.

---

### User Story 3 — Download Completed Results as JSONL (Priority: P1)

Once a job reaches `completed` status, the caller downloads a JSONL file where each line corresponds to one submitted item, in submission order, containing either the inference output or an error record.

**Why this priority**: Without result retrieval, batch submission produces no usable value.

**Independent Test**: Submit a batch of 100 items, wait for `completed` status, then download the result file. Verify that the file has exactly 100 lines, each parseable as JSON, each containing an `index` matching the original submission position and either an `output` field or an `error` field.

**Acceptance Scenarios**:

1. **Given** a `completed` job, **When** the caller requests the results, **Then** the response is a valid JSONL file where each line contains `index`, `status` (`success` or `error`), and either `output` or `error_detail`.
2. **Given** the JSONL result file, **When** parsed, **Then** every submitted item index from `0` to `N-1` is present exactly once.
3. **Given** a job that is still `running`, **When** the caller requests results, **Then** the system returns `409 Conflict` indicating the job is not yet complete.
4. **Given** a job whose results have expired (beyond retention window), **When** the caller requests results, **Then** the system returns `410 Gone`.

---

### User Story 4 — Individual Item Failures Are Recorded Without Stopping the Job (Priority: P2)

If one or more inference items fail (e.g., the model proxy returns an error, the prompt is rejected by safety filters, or a timeout occurs), those items are recorded as failed in the results. The remaining items continue to be processed and the overall job reaches `completed` status.

**Why this priority**: Batch workloads over thousands of items will inevitably encounter transient failures. Stopping the entire job on first failure would make large batches impractical.

**Independent Test**: Submit a batch where a subset of items contain deliberately malformed prompts. After completion, verify the JSONL file contains `status: error` for those items and `status: success` for well-formed items. The overall job `status` is `completed`, not `failed`.

**Acceptance Scenarios**:

1. **Given** a batch containing some items that trigger model proxy errors, **When** the job completes, **Then** those items appear in the JSONL file with `status: error` and an `error_detail` message; all other items show their inference output.
2. **Given** a batch where every item fails, **When** all processing is done, **Then** job `status` is `completed` (not `failed`), `failed_items` equals `total_items`, and every JSONL line has `status: error`.
3. **Given** a transient model proxy error on an item, **When** a retry succeeds within the retry budget, **Then** the item appears in results with `status: success`.

---

### User Story 5 — Concurrency Limit Protects the Model Proxy (Priority: P2)

The batch worker processes items using a configurable maximum number of simultaneous in-flight requests to the model proxy. Submitting a large batch never saturates the proxy beyond this ceiling.

**Why this priority**: Without a concurrency cap, a single large batch submission could cause cascading failures in the model proxy, degrading interactive (non-batch) traffic.

**Independent Test**: Submit a 1,000-item batch while monitoring concurrent requests reaching the model proxy. Verify that at no point during processing do concurrent in-flight requests exceed the configured ceiling (default 10).

**Acceptance Scenarios**:

1. **Given** a configurable concurrency limit (default: 10 concurrent requests), **When** a large batch is being processed, **Then** the number of simultaneous in-flight model proxy calls never exceeds that limit.
2. **Given** the concurrency limit is reached, **When** a slot frees up, **Then** the next queued item begins processing immediately without requiring a restart or manual intervention.
3. **Given** the concurrency limit is set to 1, **When** a batch of 100 items is submitted, **Then** items are processed strictly one at a time and the job still completes successfully.

---

### User Story 6 — Each Batch Item Is Traced in Phoenix Linked to Its Parent Job (Priority: P3)

Operators can inspect the distributed trace for any batch item in Phoenix Arize. Each item's span is a child of the overall job span, making it possible to filter by job ID and see all item spans, or drill into a single item's latency and output.

**Why this priority**: Observability is essential for diagnosing latency spikes, failure patterns, and model behaviour across large batches, but it does not block functional use of the batch API.

**Independent Test**: Submit a batch of 10 items, wait for completion, then open Phoenix and filter by `batch_job_id`. Verify 11 spans appear: 1 parent job span and 10 child item spans, each containing the item index, model name, status, and latency.

**Acceptance Scenarios**:

1. **Given** a completed batch job, **When** an operator searches Phoenix by job ID, **Then** one parent span for the job and one child span per item are visible.
2. **Given** a child item span in Phoenix, **When** inspected, **Then** it contains `batch_job_id`, `item_index`, `model`, `status`, and `latency_ms` as span attributes.
3. **Given** a failed item, **When** its span is inspected in Phoenix, **Then** the span status is `ERROR` and the error message is recorded as a span event.

---

### Edge Cases

- What happens when an empty batch (zero items) is submitted?
- How does the system handle a batch where the queue worker crashes mid-processing — do items already dequeued get retried or lost?
- What happens if a caller polls a job that belongs to a different API consumer?
- How does the system behave if results storage is full or unavailable when a job completes?
- What is the behaviour when the same job is requested for download concurrently by multiple callers?
- What happens to in-progress jobs during a worker restart or deployment?

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Callers MUST be able to submit a batch of 1 to 10,000 inference items in a single request and receive a unique `job_id` and initial `status` of `queued` within 1 second.
- **FR-002**: The system MUST process batch items asynchronously in the background without holding the submission connection open.
- **FR-003**: The system MUST expose a status endpoint that returns `job_id`, `status`, `total_items`, `completed_items`, `failed_items`, and `created_at` for any valid job.
- **FR-004**: Job status MUST progress through the states: `queued` → `running` → `completed`. A job MUST NOT transition to a terminal `failed` state due to individual item errors.
- **FR-005**: When a job reaches `completed` status, the system MUST make a JSONL result file available for download. Each line MUST contain `index`, `status`, and either `output` or `error_detail`.
- **FR-006**: A single item failure MUST NOT halt processing of remaining items. Each item is retried up to 3 times on transient errors before being recorded as failed.
- **FR-007**: The batch worker MUST enforce a configurable maximum concurrency ceiling (`MAX_CONCURRENT` environment variable, default: 10) on simultaneous in-flight LLM calls to the model proxy. No more than `MAX_CONCURRENT` parallel requests may be outstanding at any time regardless of batch size.
- **FR-008**: The batch worker process MUST use the OpenTelemetry SDK to emit one child span per batch item. Each child span MUST be linked to the parent job span and contain at minimum: `batch_job_id`, `item_index`, `model`, `status`, and `latency_ms`.
- **FR-009**: The batch worker MUST create one parent span per job at submission time using the OpenTelemetry SDK. This span MUST remain open until the job reaches a terminal state (`completed`), at which point it is closed and exported to Phoenix Arize.
- **FR-010**: Results MUST be available for download for exactly 24 hours after job completion, after which the system MUST auto-delete them. Subsequent download attempts MUST return `410 Gone`.
- **FR-011**: Callers MUST NOT be able to access jobs or results belonging to other API consumers.
- **FR-012**: The system MUST reject batch submissions that exceed 10,000 items with a `400 Bad Request` response.
- **FR-013**: Attempting to download results for a job not yet in `completed` state MUST return `409 Conflict`.

### Key Entities

- **Batch Job**: Represents a single submission. Attributes: `job_id` (UUID), `consumer_id`, `model`, `status`, `total_items`, `completed_items`, `failed_items`, `created_at`, `started_at`, `completed_at`, `results_expires_at`, `trace_id`.
- **Batch Item**: One inference request within a job. Attributes: `job_id`, `item_index` (0-based), `input_payload`, `output_payload`, `status` (`pending`, `success`, `error`), `error_detail`, `retry_count`, `latency_ms`.
- **JSONL Result Record**: One line in the output file. Fields: `index`, `status` (`success` or `error`), `output` (when success), `error_detail` (when error).

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Callers receive a `job_id` within 1 second of submitting any valid batch, regardless of batch size.
- **SC-002**: A batch of 1,000 items completes end-to-end within a time bounded by `(1000 / concurrency_limit) × average_item_latency + 20%` — demonstrating predictable throughput.
- **SC-003**: Every submitted item appears in the JSONL result file exactly once — zero items are silently dropped.
- **SC-004**: A batch containing 10% deliberately failing items completes with `status: completed` and the JSONL file contains error records for exactly those items and output records for the rest.
- **SC-005**: At no point during processing of any batch does the number of concurrent in-flight model proxy requests exceed the configured concurrency ceiling.
- **SC-006**: For any completed batch job, all item spans are visible in Phoenix under the parent job span within 60 seconds of job completion.
- **SC-007**: Polling a completed job's status endpoint 24 hours after completion still returns the job record (result file availability may vary per FR-010).

---

## Assumptions

- All batch API endpoints are exposed through the existing Kong gateway at `:8080` and subject to the same consumer authentication and rate-limiting as real-time endpoints.
- Batch items use the same model names as the real-time catalogue (`gpt-4o`, `claude-sonnet`, etc.) — no separate model list is needed.
- Each item in a batch is an independent, stateless inference request — items do not depend on each other's output.
- The Redis instance on port 6380 with `noeviction` policy is the designated work queue for batch items. This is a hard dependency — no alternative queue backend is in scope.
- Results (JSONL files) are stored on the local filesystem or object-compatible storage accessible to the worker; the exact storage backend is an implementation detail.
- The default concurrency ceiling of 10 is chosen to protect the model proxy during normal operation; operators can tune this via environment variable without a code change.
- Items are processed in best-effort order; strict FIFO per-item is not guaranteed, but the output JSONL is always ordered by `index` regardless of processing order.
- Job and item records are persisted in the dedicated `litellm` PostgreSQL database (or a new `batch` database if schema isolation is needed — to be decided during planning).
- The platform UI does not need to be updated as part of this feature; a future portal-backend or UI feature can surface batch job management.
- Trace context (`traceparent`) from the submission request is captured and used as the root for the parent job span.
