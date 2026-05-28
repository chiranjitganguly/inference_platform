# Research: Response Caching Layer

**Feature**: `007-response-caching` | **Date**: 2026-05-28

All decisions below were fully resolved from user-provided context plus constitution review. No external research was required.

---

## Decision 1: Cache Implementation Strategy

**Decision**: Use LiteLLM's built-in Redis cache (`cache_params.type: redis`) with no custom middleware.

**Rationale**: LiteLLM v1.52.0 ships a production-ready Redis cache client that handles key computation, TTL-based expiry, cache-hit response replay, Prometheus counter emission (`litellm_cache_hit_count`), and callback suppression on hits (no Phoenix/Langfuse trace for cache hits). Building a custom caching layer between Kong and LiteLLM would duplicate this logic, bypass LiteLLM's cache key semantics (which are consistent with its model routing logic), and violate constitution §2.1 which prohibits shortcutting the chain.

**Alternatives considered**:
- Custom Redis middleware at the Kong layer — rejected: would bypass Guardrails, violate §2.1
- Application-level caching in a new FastAPI service — rejected: adds a new service with no benefit over LiteLLM native caching; would add ~50 MB to memory budget for no gain
- In-process LRU cache in LiteLLM — rejected: not persistent across restarts; not distributed across replicas; LiteLLM's built-in option already uses Redis

---

## Decision 2: Cache Key Composition

**Decision**: Cache key = `hash(model_name + messages_array + temperature)`. No other fields.

**Rationale**: User-specified. Temperature is the primary sampling parameter that determines response variation; other parameters (`top_p`, `max_tokens`, `user`, metadata) are excluded. Messages array comparison is order-sensitive and byte-exact — this matches LiteLLM's native key computation. Temperature omitted from the request body is treated as `1.0` for keying (LiteLLM default).

**Alternatives considered**:
- Include `max_tokens` in key — rejected: increases cache fragmentation with marginal correctness benefit; same temperature + same prompt should cache the same response regardless of output length constraint
- Include all request parameters — rejected: drastically reduces cache hit rate; metadata fields vary per request and are not semantically meaningful for response identity

---

## Decision 3: Two Separate Redis Instances

**Decision**: `redis-cache` on port 6379 (allkeys-lru, 256 MB maxmemory, no persistence) and `redis-queue` on port 6380 (noeviction, separate from cache).

**Rationale**: The two Redis use cases have conflicting eviction requirements. The cache store must evict old entries under memory pressure (allkeys-lru is the correct policy — always evict the least-recently-used key when memory is full). The batch queue must never evict job payloads; accidental eviction would silently drop batch jobs. Running both on the same instance with any eviction policy creates a correctness hazard for one use case. The constitution §3 already documents both instances; this feature brings them into docker-compose.yml.

**Alternatives considered**:
- Single Redis with keyspace separation — rejected: cannot apply different eviction policies per keyspace in Redis; would require `noeviction` (unsafe for cache under memory pressure) or `allkeys-lru` (unsafe for job queue)
- Redis Cluster — rejected: overkill for local development; single-node is sufficient for the targeted scale

---

## Decision 4: TTL Configuration

**Decision**: Default TTL = 3600 seconds, set via `cache_params.ttl: os.environ/LITELLM_CACHE_TTL` in `services/litellm/config.yaml`. Operator overrides by setting `LITELLM_CACHE_TTL` in `.env`.

**Rationale**: User-specified. LiteLLM's `os.environ/VAR_NAME` syntax resolves the value from the environment at startup, satisfying FR-004 (configurable without redeploying application code). TTL applies to new cache writes; existing entries retain their original TTL.

**Alternatives considered**:
- Hardcoded TTL in config — rejected: FR-004 requires operator override without code change
- Redis `EXPIRE` set externally — rejected: redundant with LiteLLM's built-in TTL management

---

## Decision 5: Cache Namespace

**Decision**: `cache_params.namespace: llm_cache` in LiteLLM config.

**Rationale**: User-specified. Namespacing all LiteLLM cache keys under `llm_cache:` prevents key collisions if additional Redis consumers (e.g., session cache, rate limiter state) are added to the same Redis instance in the future. LiteLLM prepends the namespace as a key prefix.

---

## Decision 6: Prometheus Observability

**Decision**: Use LiteLLM's native `litellm_cache_hit_count` Prometheus counter. No custom instrumentation needed.

**Rationale**: User-specified. LiteLLM v1.52.0 emits `litellm_cache_hit_count` (cache hits) and related counters via its `prometheus` callback, which is already registered in `litellm_settings.callbacks`. The Prometheus scrape of LiteLLM metrics at `:4000/metrics` captures this counter. No additional code is required.

**Note**: The `litellm_cache_hit_count` counter increments on every cache hit. Cache misses are observable as `litellm_request_total` - `litellm_cache_hit_count` or via LiteLLM's native miss counter if available. Both are queryable via Prometheus without accessing the Redis store directly (SC-007).

---

## Decision 7: Phoenix / Langfuse Bypass on Cache Hits

**Decision**: LiteLLM's native caching behaviour already satisfies FR-011 — cache hits return the stored response without invoking provider callbacks. No additional configuration needed.

**Rationale**: LiteLLM's cache implementation short-circuits the provider call path. The `arize_phoenix` and `langfuse` callbacks are invoked only when a provider response is received. Cache hits return before reaching the callback invocation point. This means Phoenix spans and Langfuse traces are created only for genuine provider calls (cache misses and streaming requests), which is exactly the requirement. Verified against LiteLLM v1.52.0 source: `router.completion()` returns cached response from `cache.get_cache()` before `async_success_handler()` is called.

---

## Decision 8: Streaming Bypass

**Decision**: Already enforced by feature 006's `supported_call_types` list in `cache_params`. No additional changes needed.

**Rationale**: Feature 006 added `supported_call_types: [acompletion, completion, aembedding, embedding]` which excludes streaming call types (`astreaming_completion`, `streaming_completion`). This means LiteLLM never reads from or writes to cache for streaming requests. FR-005 is already satisfied.
