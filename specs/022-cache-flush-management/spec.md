# Feature Specification: Cache Flush Management

**Feature Branch**: `022-cache-flush-management`

**Created**: 2026-06-07

**Status**: Draft

**Input**: User description: "Build cache management endpoints that allow platform operators to flush the response cache entirely or selectively by model. After a model configuration change or prompt update the operator must invalidate stale cached responses so fresh results are returned. Only the platform master key may perform cache flush operations."

## Clarifications

### Session 2026-06-07

- Q: What are the canonical endpoint paths? → A: `DELETE /cache/flush` (full flush); `DELETE /cache/flush?model={name}` (model-scoped flush)
- Q: What HTTP status does a non-master key receive? → A: HTTP 403 Forbidden
- Q: What does the response body field for deleted count look like? → A: JSON integer field `keys_deleted`
- Q: What is guaranteed after a successful flush? → A: The next identical request is a cache miss — no stale entry is served
- Q: Is the GET /cache/stats endpoint in scope? → A: Deferred to planning — not part of the flush MVP; flush operations are the primary deliverable

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Full Cache Flush (Priority: P1)

A platform operator has just deployed a global prompt update via Langfuse that applies across all models. They need to ensure no callers receive stale cached responses from before the change. The operator calls `DELETE /cache/flush` with the master key and all cached inference responses are immediately invalidated. Subsequent requests to any model return freshly generated responses (cache miss).

**Why this priority**: This is the emergency "big red button" — the operator must be able to guarantee a clean slate instantly after any cross-cutting change. Without it, stale responses can persist for the full TTL even after the platform has been updated.

**Independent Test**: Populate the cache with at least one entry, call `DELETE /cache/flush` with a valid master key, then re-issue a previously-cached request and confirm a new response is generated (cache miss — not served from cache).

**Acceptance Scenarios**:

1. **Given** the cache contains responses for multiple models, **When** the operator calls `DELETE /cache/flush` with the master key in the `Authorization` header, **Then** all cached entries are removed and the response is HTTP 200 with `{"keys_deleted": <count>}`.
2. **Given** the cache is already empty, **When** the operator calls `DELETE /cache/flush`, **Then** the endpoint returns HTTP 200 with `{"keys_deleted": 0}` (idempotent, not an error).
3. **Given** an API key that is not the master key, **When** `DELETE /cache/flush` is called, **Then** the endpoint returns HTTP 403 Forbidden and no cache entries are touched.
4. **Given** no API key is provided in the `Authorization` header, **When** `DELETE /cache/flush` is called, **Then** the endpoint returns HTTP 401 Unauthorized and no cache entries are touched.
5. **Given** a successful flush, **When** the same request that was previously cached is re-issued by a caller, **Then** the response is freshly generated (cache miss), not the previously cached value.

---

### User Story 2 — Model-Scoped Cache Flush (Priority: P2)

A platform operator has updated the configuration for `gpt-4o`. Only responses cached for that specific model are now stale. The operator calls `DELETE /cache/flush?model=gpt-4o` to invalidate only `gpt-4o` entries, leaving cached responses for other models intact and avoiding unnecessary upstream load.

**Why this priority**: Targeted flush is the preferred operation in most real-world cases — it minimises disruption while still guaranteeing freshness for the affected model.

**Independent Test**: Populate the cache with entries for two different models. Call `DELETE /cache/flush?model=gpt-4o`. Confirm entries for `gpt-4o` are gone (cache miss on re-issue) and entries for the other model are still served from cache (cache hit on re-issue).

**Acceptance Scenarios**:

1. **Given** the cache has entries for `gpt-4o` and `claude-sonnet`, **When** the operator calls `DELETE /cache/flush?model=gpt-4o` with the master key, **Then** only `gpt-4o` entries are removed; `claude-sonnet` entries remain cached and the response is HTTP 200 with `{"keys_deleted": <count>, "model": "gpt-4o"}`.
2. **Given** a model name with no cached entries, **When** `DELETE /cache/flush?model=<name>` is called, **Then** the response is HTTP 200 with `{"keys_deleted": 0, "model": "<name>"}` (idempotent).
3. **Given** a model name not in the platform model catalogue, **When** the scoped flush is called, **Then** the endpoint returns HTTP 422 with a body listing valid model names; no cache operation is performed.
4. **Given** a successful model-scoped flush, **When** the same request for that model is re-issued, **Then** the response is freshly generated (cache miss).

---

### Edge Cases

- What happens when a flush request arrives while a cache write is in progress? The flush must not leave the cache in a partially-cleared state — atomicity is required for the targeted model scope.
- What happens if the cache backend is temporarily unreachable when a flush is requested? The endpoint must return HTTP 503 with a descriptive error rather than silently failing or returning a partial `keys_deleted` count.
- What if the `model` query parameter contains special characters or an excessively long string? The endpoint must reject the request with HTTP 422 before attempting any cache operation.
- What if a full flush and a model-scoped flush arrive concurrently? The full flush takes precedence; the model-scoped flush returns `{"keys_deleted": 0}` because the full flush already cleared those entries.
- What happens if a flush is requested when caching is globally disabled? The endpoint returns HTTP 200 with `{"keys_deleted": 0}` rather than an error.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose `DELETE /cache/flush` that removes all cached inference responses when the `Authorization` header carries the platform master key (`LITELLM_MASTER_KEY`).
- **FR-002**: The system MUST expose `DELETE /cache/flush?model={name}` that removes only cached responses whose cache key matches the supplied model name prefix, when called with the platform master key.
- **FR-003**: Both endpoints MUST return HTTP 403 Forbidden when a valid but non-master API key is supplied, and HTTP 401 Unauthorized when no key is supplied; in both cases no cache entries are modified.
- **FR-004**: Both endpoints MUST return HTTP 200 with a JSON body containing an integer field `keys_deleted` indicating how many cache entries were removed.
- **FR-005**: The model-scoped endpoint MUST validate the supplied model name against the platform model catalogue and return HTTP 422 with valid model names listed if the supplied name is unrecognised.
- **FR-006**: Both endpoints MUST be idempotent — flushing an already-empty cache or an empty model scope returns HTTP 200 with `{"keys_deleted": 0}`, never an error.
- **FR-007**: After a successful flush, the next request that would have hit the flushed cache entries MUST result in a cache miss and a fresh upstream call; no stale entry may be served.
- **FR-008**: All flush operations MUST be logged as audit events including: timestamp, operator key hash (not the raw key), scope (`all` or model name), and `keys_deleted` count.
- **FR-009**: Both endpoints MUST be accessible via Kong at `:8080/cache/flush` and honour the existing gateway authentication and rate-limit plugins already applied to the platform; they MUST NOT be accessible by bypassing Kong.

### Key Entities

- **CacheFlushRequest**: Scope (`all` or `model`), model name (optional), requesting key hash, timestamp.
- **CacheFlushResult**: Scope, model name (if scoped), `keys_deleted` integer, flush duration.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `DELETE /cache/flush` completes and returns `keys_deleted` within 5 seconds regardless of cache size.
- **SC-002**: `DELETE /cache/flush?model={name}` removes only entries matching the named model — zero entries for other models are affected (verified by re-issuing requests for both models after the scoped flush).
- **SC-003**: A request with a non-master API key is rejected with HTTP 403 in under 100 ms, before any cache operation is attempted.
- **SC-004**: 100% of flush operations produce an audit log entry; no flush can occur without a corresponding log record.
- **SC-005**: Invalid model name supplied to the scoped flush always returns HTTP 422 with a list of valid model names; no cache operation is performed.
- **SC-006**: After a successful flush, 0% of re-issued requests that previously hit the cache are served from the (now-cleared) cache — all result in fresh upstream calls.

## Assumptions

- The response cache uses a Redis namespace prefix (`llm_cache:`) that can be targeted with a `SCAN` + `DEL` pattern; model-scoped flush filters by `llm_cache:<model-name>:*` key prefix.
- The platform master key (`LITELLM_MASTER_KEY`) is already provisioned in Vault and passed to the service at startup; no new key provisioning is required by this feature.
- The response cache is the Redis instance on port 6379 (the LiteLLM caching layer); the Redis queue on port 6380 used for batch jobs is out of scope for flush operations.
- The model catalogue (list of valid model names) is available at runtime for validation of the `?model=` parameter.
- Caching of streaming responses is already excluded by the platform config; this feature covers only non-streaming (standard) inference response cache entries.
- The GET /cache/stats endpoint is deferred — not part of this feature's scope; operators use the `keys_deleted` count in flush responses to confirm cache state.
