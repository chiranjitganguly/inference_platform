# Feature Specification: Response Caching Layer

**Feature Branch**: `007-response-caching`

**Created**: 2026-05-28

**Status**: Draft

**Input**: User description: "Build a caching layer that returns stored responses for repeated identical requests without calling the LLM provider, achieving sub-10ms latency and zero provider cost on cache hits. Cache hits must occur when the same model, messages array, and temperature are submitted a second time. A different messages array or model must always cause a fresh provider call. Cached responses must expire automatically after a configurable period."

## Clarifications

### Session 2026-05-28

- Q: What parameters compose the cache key? → A: model + messages array + temperature only; no other request fields contribute to the key.
- Q: What is the default TTL and how is it configured? → A: 3600 seconds default; configurable by the platform operator via environment variable without redeploying application code.
- Q: Are streaming requests ever cached? → A: Never — `stream: true` requests bypass the cache in both directions (no read, no write).
- Q: How are cache hits tracked in observability? → A: Cache hits MUST increment a dedicated Prometheus counter; cache misses MUST increment a separate Prometheus counter.
- Q: Do cache hits create traces in Phoenix or Langfuse? → A: No — only the original provider call creates a Phoenix span and a Langfuse trace; cache hits generate neither.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Cache Hit Returns Stored Response (Priority: P1)

A caller submits an identical request (same model, messages, and temperature) a second time and receives the stored response instantly — no provider call is made and the response arrives in under 10 ms.

**Why this priority**: Directly delivers the core value proposition: cost elimination and latency reduction on repeated requests. Without this, the feature does not exist.

**Independent Test**: Send the same request twice. Verify the second response arrives in under 10 ms and matches the first response exactly. Confirm no provider API call was made on the second request. Confirm the cache-hit Prometheus counter incremented by 1.

**Acceptance Scenarios**:

1. **Given** a prior successful request for model M, messages A, temperature T, **When** the identical request is submitted again, **Then** the response is returned from cache in under 10 ms with no provider call and the cache-hit counter increments.
2. **Given** two identical requests submitted concurrently, **When** the first resolves and is stored, **Then** subsequent identical requests return the cached result without a second provider call.
3. **Given** a cached response exists, **When** the caller inspects response metadata, **Then** a cache status indicator distinguishes the hit response from a fresh provider response.
4. **Given** a cache hit is served, **When** the observability stack is queried, **Then** no new Phoenix span and no new Langfuse trace are created for that request.

---

### User Story 2 — Cache Miss Triggers Fresh Provider Call (Priority: P1)

A caller submits a request that differs from any stored entry — different model, different messages, or different temperature — and the platform fetches a fresh response from the LLM provider.

**Why this priority**: Correctness gate. If the cache incorrectly serves a stored response for a different request, callers receive wrong answers. This is a safety constraint, not an enhancement.

**Independent Test**: Send request A, then request B (differing by model, messages, or temperature). Verify request B results in a provider call and returns a distinct response appropriate to request B. Confirm the cache-miss Prometheus counter incremented.

**Acceptance Scenarios**:

1. **Given** a cached response for model M1 and messages A, **When** a request arrives for model M2 with the same messages A, **Then** the provider is called and a fresh response is returned.
2. **Given** a cached response for messages A, **When** a request arrives for messages B (any change — added, removed, or modified message), **Then** the provider is called and a fresh response is returned.
3. **Given** a cached response at temperature 0.7, **When** a request arrives for the same model and messages at temperature 0.0, **Then** the provider is called and a fresh response is returned.
4. **Given** any cache miss, **When** the provider responds successfully, **Then** the response is stored in the cache for future identical requests and a Phoenix span and Langfuse trace are created for this provider call.

---

### User Story 3 — Cached Responses Expire Automatically (Priority: P2)

Cached entries expire after a configurable time-to-live (TTL). After expiry, the next identical request fetches a fresh provider response and replaces the stale entry.

**Why this priority**: Without expiry, callers receive outdated responses indefinitely. TTL-based expiry keeps responses fresh and is required for correctness over time. Dependent on US1 being live.

**Independent Test**: Configure a short TTL via environment variable (e.g., 30 seconds). Send an identical request before and after the TTL elapses. Verify the post-expiry request triggers a provider call and the response is re-cached.

**Acceptance Scenarios**:

1. **Given** a cached response with TTL T, **When** T seconds elapse, **Then** the entry is no longer served from cache and the next identical request calls the provider.
2. **Given** a cached response that has not yet expired, **When** the identical request arrives, **Then** the cached response is returned regardless of how close to expiry it is.
3. **Given** the platform operator sets a new default TTL via environment variable, **When** new requests are cached, **Then** they use the updated TTL; existing entries retain their original TTL.

---

### User Story 4 — Non-Cacheable Requests Always Bypass Cache (Priority: P2)

Streaming requests (`stream: true`) are never read from or written to the cache store.

**Why this priority**: Streaming responses cannot be reliably stored and replayed as a single cached blob. This story prevents correctness failures for streaming callers and aligns with the existing streaming constraint (FR-013 from feature 006).

**Independent Test**: Send a streaming request and a non-streaming identical request for the same prompt. Verify the streaming request never produces a cache hit and is never stored. Verify the non-streaming request is cached normally.

**Acceptance Scenarios**:

1. **Given** a streaming request (`stream: true`), **When** submitted any number of times, **Then** no cache lookup is performed and no response is stored; the cache-hit counter does not increment.
2. **Given** a prior cached non-streaming response, **When** the same prompt is submitted with `stream: true`, **Then** the streaming request calls the provider and does not return the cached blob.

---

### Edge Cases

- What happens when the cache store is unavailable (e.g., connection failure)? → The platform falls through to the provider and returns a fresh response; no error is surfaced to the caller.
- What happens if the cache store runs out of memory or storage? → Eviction removes old entries; new entries continue to be stored normally.
- What happens when two identical requests arrive simultaneously before either is cached? → Both may call the provider; the last write wins and subsequent identical requests are served from cache. No correctness violation occurs.
- What happens when the provider returns an error? → Error responses are not cached; the next identical request retries the provider.
- What happens with very large responses? → Entries exceeding a size limit are not cached; the provider is always called for those requests.
- What happens when temperature is omitted from a request? → The platform treats the omission as a deterministic default (temperature = 1.0) for cache key purposes; requests omitting temperature are keyed identically to requests explicitly setting temperature = 1.0.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The platform MUST return a stored response for any non-streaming request where the model, messages array (including role and content for every message in order), and temperature are identical to a previously cached request that has not expired.
- **FR-002**: The platform MUST call the LLM provider and NOT serve a cached response whenever the model, any message in the messages array, or the temperature differs from all cached entries.
- **FR-003**: Cached responses MUST expire automatically after a configurable time-to-live (TTL) measured in seconds; after expiry the entry MUST be treated as a cache miss.
- **FR-004**: The default TTL MUST be 3600 seconds and MUST be overridable by the platform operator via environment variable without redeploying application code.
- **FR-005**: Streaming requests (`stream: true`) MUST never be read from or written to the cache, regardless of whether an identical non-streaming entry exists.
- **FR-006**: Every response MUST include a metadata indicator distinguishing a cache hit from a fresh provider response, visible to the caller in the response without inspecting internal logs.
- **FR-007**: If the cache store is unavailable, the platform MUST fall through to the provider transparently — callers MUST NOT receive an error attributable to the cache being down.
- **FR-008**: Error responses from the provider MUST NOT be stored in the cache.
- **FR-009**: The cache key MUST be derived exclusively from: model name, the full ordered messages array (all roles and content values), and temperature. No other request fields (e.g., `max_tokens`, `user`, metadata) affect cache key computation.
- **FR-010**: Every cache hit MUST increment a dedicated Prometheus counter; every cache miss MUST increment a separate Prometheus counter. These counters MUST be queryable from the existing observability stack without accessing the cache store directly.
- **FR-011**: Cache hits MUST NOT create a Phoenix span or a Langfuse trace. Only requests that result in a provider call (cache misses and streaming requests) create observability traces.

### Key Entities

- **Cache Entry**: A stored response. Key attributes: cache key (derived from model + messages + temperature), stored response body, creation timestamp, TTL, expiry timestamp.
- **Cache Key**: A deterministic identifier computed from the model name, the full ordered messages array, and the temperature value. Identical inputs always produce the same key; any difference produces a different key.
- **TTL Configuration**: The operator-defined default time-to-live applied to new cache entries. Set via environment variable; does not retroactively alter existing entries.
- **Cache Status Indicator**: A response-level field that signals whether the response was served from cache (`hit`) or fetched from the provider (`miss`).
- **Cache Hit Counter**: A Prometheus counter that increments once per request served from cache. Labels include at minimum: model name.
- **Cache Miss Counter**: A Prometheus counter that increments once per request that bypasses the cache and calls the provider. Labels include at minimum: model name, miss reason (expired / not-found / streaming-bypass).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Cache hit responses are returned to the caller in under 10 ms end-to-end (measured from request receipt to response delivery at the gateway).
- **SC-002**: Zero provider API calls are made for requests that result in a cache hit; confirmed via provider call counters in observability.
- **SC-003**: Cache misses (new or expired entries) trigger exactly one provider call and the response is available from cache for the next identical request.
- **SC-004**: Cached entries are no longer served after their TTL has elapsed; the first post-expiry request triggers a provider call within normal provider latency.
- **SC-005**: Streaming requests (`stream: true`) show a cache hit counter increment of 0 at all times.
- **SC-006**: When the cache store is unavailable, 100% of requests continue to be served by the provider with no increase in caller-visible error rate.
- **SC-007**: Cache hit count and miss count are queryable via Prometheus without accessing the cache store directly.
- **SC-008**: No Phoenix span and no Langfuse trace are created for cache-hit requests; trace count in Phoenix/Langfuse matches provider call count, not total request count.

## Assumptions

- The cache store (Redis) is already deployed as part of the core infrastructure from prior features; no new storage service is introduced.
- TTL defaults to 3600 seconds (1 hour) unless overridden by the operator via environment variable.
- The messages array comparison is order-sensitive and byte-exact — reordering messages produces a cache miss.
- `temperature` is the only sampling parameter included in the cache key; `top_p`, `max_tokens`, and similar generation parameters are excluded (assumption: temperature is the primary determinant of response variation for the targeted use cases).
- Temperature omitted from the request body is treated as temperature = 1.0 for cache key computation.
- Non-streaming requests only are in scope for caching; embedding requests follow separate caching rules already established in feature 006 and are not modified by this feature.
- The platform operator is the only actor who configures TTL; end-user callers cannot set per-request TTL via the request body.
- Cache entries store the complete response body as delivered to the caller; no response fields are stripped before storage.
- Per-entry size limits are enforced by the cache store's existing eviction policy; this feature does not introduce custom size validation.
- LiteLLM's built-in Redis caching is the implementation mechanism; this spec does not constrain the internal implementation approach beyond the behavioural requirements stated above.
