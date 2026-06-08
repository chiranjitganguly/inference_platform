# Implementation Plan: Guardrails Bypass Flag

**Branch**: `023-guardrails-bypass-flag` | **Date**: 2026-06-08 | **Spec**: [spec.md](spec.md)

## Summary

Add an optional `guardrails` boolean field to `POST /v1/chat/completions`. When `false`, the guardrails service skips all validation gates and proxies the request directly to LiteLLM. When `stream: true` is also set, a true SSE streaming passthrough is used (`httpx client.stream()`) so chunks are forwarded in real time without buffering. The flag is stripped before forwarding. Default (`true` or absent) preserves all existing behaviour.

Also includes a reference client (`app/test.py`) demonstrating the streaming bypass path using httpx directly with the explicit `/v1/chat/completions` endpoint and `.env`-based key loading.

## Technical Context

**Language/Version**: Python 3.11 — FastAPI (guardrails service)

**Primary Dependencies**:
- `httpx` — already present in guardrails service
- `fastapi` — already present
- `python-dotenv` — new dependency in `app/test.py` (reference client only)
- `openai` — new dependency in `app/test.py` (not used in the final version; reference client uses httpx directly)

**Storage**: None. No new Redis keys, no new PostgreSQL tables.

**Target Platform**: Docker Compose `core` profile (guardrails is in all profiles that include the inference chain).

**Performance Goals**:
- Streaming first-byte latency with bypass: equivalent to direct LiteLLM call (no additional buffering)
- Bypass flag extraction: < 1 ms overhead (single JSON parse, already done for validation)

**Constraints**:
- The `guardrails` key must be stripped before forwarding — LiteLLM will reject unknown fields or pass them to the model API which may reject them
- The httpx client and stream context must be kept alive for the lifetime of the FastAPI `StreamingResponse` — closed in the generator's `finally` block
- The audit log still fires on the bypass path; post-inference fields (`pii_entity_count`, `scanner_blocked`) remain 0 since scanning is skipped

## Architecture Decision

### Why `__aenter__` / `__aexit__` for streaming passthrough

The streaming passthrough needs both the response status code (to construct `StreamingResponse`) and a live upstream connection (to forward bytes in real time). This requires:

1. Opening the httpx client and stream context *before* creating `StreamingResponse` (to get `upstream.status_code`)
2. Keeping both alive *after* `StreamingResponse` is returned (while FastAPI drains the generator)

Using `async with client.stream(...) as upstream:` inside the generator would not give us the status code before the `StreamingResponse` is created. The solution is to call `__aenter__` manually to enter both context managers, capture the status, then delegate cleanup to the generator's `finally` block via `__aexit__`.

### Why the flag is extracted only for `POST /v1/chat/completions`

The bypass flag is only meaningful on the inference path. Embeddings, health, and admin endpoints either have no validation gates or are not affected by the flag.

### Why the flag is stripped before forwarding

LiteLLM proxies requests to upstream model APIs (OpenAI, Anthropic, etc.). These APIs reject unknown fields. The `guardrails` field must be removed before the request leaves the platform.

## Project Structure

### Documentation (this feature)

```text
specs/023-guardrails-bypass-flag/
├── plan.md                         # This file
├── spec.md                         # Feature specification
├── research.md                     # Design decisions
├── data-model.md                   # Code entities + flow diagrams
├── quickstart.md                   # Acceptance tests
├── checklists/
│   └── requirements.md             # FR traceability
├── contracts/
│   └── guardrails-bypass.md        # Request/response contract
└── tasks.md                        # Implementation tasks (all complete)
```

### Source Code Changes

```text
services/guardrails/main.py
  ├── _streaming_passthrough()          NEW helper function
  │     Opens httpx client + stream context manually (__aenter__)
  │     Captures upstream.status_code and headers
  │     Returns StreamingResponse backed by async generator _gen()
  │     _gen() forwards aiter_bytes() chunks; finally block calls __aexit__
  │
  └── proxy()                           MODIFIED
        Extracts + strips `guardrails` flag from body (chat completions only)
        Moves `headers` computation before the guardrails check
        Adds bypass branch: streaming → _streaming_passthrough(); buffered → direct client.request()
        Existing validation path (guardrails=True) unchanged

app/test.py                           NEW — reference streaming client
  Uses httpx directly to POST to http://localhost:8080/v1/chat/completions
  Loads PLATFORM_API_KEY from project .env via python-dotenv
  Sets guardrails: false, stream: true
  Parses SSE lines, handles error chunks (no `choices` key)
```

## Task Groups

### Group A — Core implementation (sequential, single file)

- Parse and strip `guardrails` flag in `proxy()`
- Add `_streaming_passthrough()` helper
- Add non-streaming bypass branch in `proxy()`

### Group B — Reference client (independent)

- `app/test.py` with httpx SSE streaming, .env loading, error handling

## Acceptance Criteria

| Criterion | Verification |
|---|---|
| SC-001: True streaming | Timestamp each `data:` chunk — multiple distinct timestamps for a long response |
| SC-002: Flag stripped | LiteLLM logs show no `guardrails` field in forwarded body |
| SC-003: No regression | All 018–020 acceptance tests pass unmodified |
| SC-004: Audit coverage | Loki: `{service="guardrails"} \|= "inference_request"` — bypass requests appear |
| SC-005: Cleanup | No open httpx connections after response is drained or client disconnects |
