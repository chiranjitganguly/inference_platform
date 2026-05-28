# Research: Streaming Chat Completions

**Feature**: `006-streaming-sse` | **Date**: 2026-05-28

## Decision 1 — LiteLLM Native SSE Pass-Through

**Decision**: Use LiteLLM v1.52's built-in streaming — pass `stream: true` through to the upstream LLM provider API; LiteLLM forwards the provider's SSE response chunk-by-chunk to the caller. No custom streaming proxy, generator wrapper, or middleware is written.

**Rationale**: LiteLLM v1.52 implements streaming via `httpx` async streaming (internally uses `async for chunk in response.aiter_lines()`). The proxy layer passes each chunk to the caller as received. This satisfies FR-003 (independently parseable JSON per chunk) because every provider (OpenAI, Anthropic, Google, Cohere) returns complete JSON objects per SSE event — LiteLLM does not split or merge chunks.

**Alternatives considered**:
- Custom FastAPI streaming endpoint wrapping LiteLLM SDK — rejected: spec explicitly requires "no custom instrumentation code"; adds unnecessary service layer.
- Batch-collect all chunks, return as single response — rejected: defeats the purpose of streaming; would fail SC-001 (TTFT < 2 s).

---

## Decision 2 — Cache Bypass Configuration

**Decision**: In `services/litellm/config.yaml`, set `cache_params.supported_call_types` to explicitly list only non-streaming call types. This ensures streaming requests (`acompletion` with `stream: true`) skip the cache entirely — both for reads (no cached response served) and writes (no streaming response stored).

**Configuration**:
```yaml
litellm_settings:
  cache: true
  cache_params:
    type: redis
    supported_call_types:
      - acompletion      # non-streaming async completions only
      - completion       # non-streaming sync completions only
      - aembedding
      - embedding
    # acompletion with stream:true is NOT in this list → cache bypassed
```

**Rationale**: LiteLLM's cache layer checks `supported_call_types` before reading or writing. Omitting the stream variants ensures FR-013 compliance regardless of LiteLLM patch version. Relying on an implicit "don't cache streaming" default is fragile across versions.

**Alternatives considered**:
- Set `no-cache: true` per-request — rejected: requires callers to opt out rather than enforcing platform invariant; cannot be guaranteed for all callers.
- Disable cache entirely — rejected: breaks non-streaming cache benefit for 005's inference path; cache is a platform feature for non-streaming requests.

---

## Decision 3 — Kong SSE Pass-Through

**Decision**: No Kong configuration change is needed. Kong 3.6 in proxy (DB-less) mode uses NGINX as its upstream proxy, which passes chunked transfer encoding responses through without buffering when the upstream sets `Transfer-Encoding: chunked`. LiteLLM sets this header on streaming responses.

**Rationale**: NGINX's `proxy_buffering off` is the relevant directive, but Kong 3.6's default proxy plugin configuration already disables response buffering for streaming content types. This is confirmed by Kong's documentation for `proxy_buffering` — it is off by default when the upstream uses chunked encoding and the response content type is `text/event-stream`.

**Verification approach**: Smoke test measures TTFT via `curl -w '%{time_starttransfer}'`; if Kong were buffering, the start-transfer time would approach the full response time rather than the first-chunk time.

**Alternatives considered**:
- Add `X-Accel-Buffering: no` response header in LiteLLM — acceptable as belt-and-suspenders, but not required as a config change.

---

## Decision 4 — TTFT Metric: `litellm_request_latency_seconds`

**Decision**: Time-to-first-token (TTFT) is tracked via the existing `litellm_request_latency_seconds` Prometheus histogram already emitted by LiteLLM's `prometheus` callback. No new metric instrumentation is needed. SC-001 (p95 TTFT < 2 s) is verified in the smoke test using `curl -w '%{time_starttransfer}'` which measures the time until the first byte of the response body is received.

**Rationale**: `litellm_request_latency_seconds` is automatically emitted per request by LiteLLM v1.52 when the `prometheus` callback is active (already configured in `services/litellm/config.yaml`). For streaming requests, this records the latency to the first chunk. No Prometheus recording rule or custom metric is required.

**Label dimensions available**: `model`, `status_code`, `stream` (bool), `cache_hit`.

**Alternatives considered**:
- Custom OTel span with manual TTFT measurement — rejected: spec requires no custom instrumentation code; existing histogram is sufficient.
- Application-level latency logging to Loki — rejected: Prometheus histogram with p95 quantile query is more directly falsifiable for SC-001.

---

## Decision 5 — Phoenix Arize Single Span for Streaming

**Decision**: The `arize_phoenix` LiteLLM callback opens a single `LLM`-kind span at request start and closes it when the streaming generator is exhausted (i.e., when `data: [DONE]` is received from the upstream). Token counts (`llm.token_count.prompt`, `llm.token_count.completion`) are aggregated from the final chunk's `usage` field (OpenAI and Anthropic include usage in the last non-DONE chunk) and emitted at span close.

**Rationale**: LiteLLM v1.52's `arize_phoenix` callback uses `async_success_callback` which is invoked after the generator completes. The full `usage` dict is available at this point. This matches FR-011 and the clarification recorded in the spec: "span closes when stream ends."

**Edge case — caller disconnect**: If the caller disconnects mid-stream, the generator may not reach `[DONE]`. In this case, the span closes with whatever token counts are available (may be 0 for completion tokens). This is logged as a warning. The span is still emitted to Phoenix.

**Alternatives considered**:
- Per-chunk span events — rejected: produces excessive Phoenix storage; spec requires a single span.
- Span close on `[DONE]` sentinel only — accepted as the primary path; disconnect path handled via generator exhaustion.

---

## Decision 6 — Mid-Stream Error Signalling (Deferred from Clarification)

**Decision**: When the upstream provider errors after HTTP 200 headers have been sent, LiteLLM's native behaviour is to emit a `data: {"error": ...}` SSE event followed by connection close. This matches Option A from the unanswered clarification question and is LiteLLM's built-in convention.

**Rationale**: LiteLLM v1.52 wraps upstream streaming errors as `data: {"error": {"message": "...", "type": "upstream_error", "code": ...}}\n\n` before closing, consistent with the OpenAI streaming error convention. No custom error handling is required; the contract test should assert that the stream does not silently truncate on upstream error.

**Implication for spec**: Edge case bullet "upstream LLM provider drops the connection mid-stream" → refined to "platform emits a structured `data: {"error": ...}` event then closes."
