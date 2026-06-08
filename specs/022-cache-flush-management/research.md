# Research: Cache Flush Management

**Feature**: 022-cache-flush-management
**Date**: 2026-06-08

---

## Decision 1: Which service hosts the cache flush endpoints?

**Decision**: portal-backend (port 8092)

**Rationale**: portal-backend is already the designated admin API service — it calls LiteLLM's spend endpoints (`/global/spend/keys`, `/global/spend/models`) and is already routed through Kong. Adding cache management endpoints here is architecturally coherent and requires no new container, Dockerfile change, or docker-compose profile entry. The precedent for admin-service-to-LiteLLM calls is already established in the codebase (`services/portal-backend/main.py` lines 112–113).

**Alternatives considered**:
- *Guardrails service*: Guardrails is an inference pipeline service; adding admin operations there conflates two unrelated concerns and adds Redis as a new dependency to a latency-sensitive service.
- *New dedicated admin service*: Unnecessary complexity — portal-backend already exists for exactly this purpose.
- *Direct Kong → LiteLLM route*: LiteLLM's native `/cache/flush` endpoint does not support model-scoped prefix filtering, making this insufficient for US2.

---

## Decision 2: How is Redis SCAN+DEL implemented?

**Decision**: Cursor-based SCAN with batched DEL, never FLUSHDB.

**Rationale**: `FLUSHDB` would destroy all Redis data on port 6379 — including session tokens, rate-limit counters, and any other non-cache keys. `SCAN` is cursor-based and non-blocking: it iterates keys matching a glob pattern in batches (`COUNT 100`) without locking the Redis server, making it safe under live load.

**Key patterns**:
- Full flush: `SCAN 0 MATCH llm_cache:* COUNT 100` + batched `DEL`
- Model-scoped flush: `SCAN 0 MATCH llm_cache:<model-name>:* COUNT 100` + batched `DEL`

**Implementation**: `redis.asyncio.Redis` (from `redis-py[async]`) with pipeline-based bulk DEL:
```
keys = []
async for key in redis.scan_iter("llm_cache:*", count=100):
    keys.append(key)
if keys:
    await redis.delete(*keys)
```

**Alternatives considered**:
- *FLUSHDB*: Rejected — destroys all Redis 6379 data including non-cache entries.
- *LiteLLM's native `/cache/flush`*: Rejected — does full flush only; no model-prefix support. Also introduces an unnecessary LiteLLM round-trip for what is a direct Redis operation.
- *`KEYS llm_cache:*`*: Rejected — `KEYS` is O(N) blocking; dangerous on large keyspaces.

---

## Decision 3: Redis key namespace convention

**Decision**: `llm_cache:<model-name>:<hash>` — model name is the second path segment.

**Rationale**: LiteLLM v1.52.0 uses a composite cache key that includes the model name as a prefix component. The exact format is `litellm_cache:<model>:<request_hash>`. The user's clarification confirms the namespace as `llm_cache:` (without the `lite` prefix used in some versions). Model-scoped flush uses `llm_cache:<model-name>:*` as the SCAN pattern.

**Risk**: If LiteLLM's key format differs from `llm_cache:<model>:*`, the SCAN pattern returns 0 keys rather than erroneously deleting wrong data — fail-safe behaviour. The first integration test verifies the pattern by checking `keys_deleted > 0` after a known cache population.

**Alternatives considered**:
- *Tag-based approach (Redis Sets)*: Would require LiteLLM to tag entries at write time. Not in scope — would require forking LiteLLM.
- *Full scan + filter by model in app code*: Less efficient; scans all keys then filters. SCAN pattern is better.

---

## Decision 4: Master key validation strategy

**Decision**: Dual-layer validation — Kong key-auth (presence check) + portal-backend LITELLM_MASTER_KEY comparison (master-only check).

**Rationale**: Kong's key-auth plugin validates that the `Authorization` header carries a known consumer key. But Kong does not know which key is the master key — that distinction is application-level. portal-backend compares the header value directly against `LITELLM_MASTER_KEY` (from environment) and returns HTTP 403 if the key is valid but not master. This matches the existing spend endpoint pattern, which delegates auth entirely to LiteLLM.

**Implementation**: In portal-backend, extract `Authorization` header, compare to `os.environ["LITELLM_MASTER_KEY"]`. If missing: 401. If present but not master: 403. If master: proceed.

**Alternatives considered**:
- *Rely solely on Kong*: Kong cannot distinguish master key from consumer keys — would require a Kong plugin or custom Lua function. Overkill for an existing Python service.
- *OPA policy*: OPA is for ABAC model-level restrictions. Master key identity is a simpler check better done in the application layer.

---

## Decision 5: Model name validation source

**Decision**: Call `GET /v1/models` on LiteLLM at validation time to get the live model catalogue.

**Rationale**: portal-backend already calls LiteLLM for spend data. Using the live `/v1/models` response ensures validation is always in sync with the model catalogue without hardcoding model names in two places. A 422 response includes the current valid model list fetched from this endpoint.

**Failure mode**: If LiteLLM is unreachable when `?model=` validation is needed, portal-backend returns HTTP 503 (same behaviour as the spend endpoint). This is acceptable — if LiteLLM is down, the cache is not serving responses anyway.

**Alternatives considered**:
- *Hardcoded list in portal-backend*: Maintenance burden — list drifts as models are added/removed.
- *Read from LiteLLM config.yaml*: Config file may not be accessible from portal-backend's container.

---

## Decision 6: Audit logging

**Decision**: Write a metadata-only Loki audit entry per flush operation via the `guardrails.audit` logger pattern (constitution §II).

**Rationale**: Flush operations are security-relevant admin actions. The audit entry mirrors the existing guardrails audit format: `timestamp`, `event_type` (`cache_flush_all` or `cache_flush_model`), `request_id`, `key_hash` (SHA-256 of the master key, never the raw value), `model` (scope or `all`), `keys_deleted`. No cache key names (which could leak request fingerprints) are logged.

**Log entry shape**:
```json
{
  "timestamp": "2026-06-08T10:00:00Z",
  "event_type": "cache_flush_all",
  "request_id": "<uuid>",
  "key_hash": "<sha256>",
  "model": "all",
  "keys_deleted": 42
}
```

---

## Decision 7: Rate limiting on the flush endpoint

**Decision**: Add a Kong rate-limit plugin scoped to the cache-flush route: 10 requests/minute per consumer.

**Rationale**: Full cache flush is a destructive operation. Accidental rapid re-calls (scripting error, retry loop) could cause a thundering-herd effect as all callers simultaneously miss the cache. 10 RPM is generous for legitimate operator use while blocking accidental loops. This is implemented as a Kong route-level plugin, not a global plugin.

**Alternatives considered**:
- *No rate limit*: Risky — a retry loop could flush the cache 60+ times per minute.
- *1 RPM*: Too restrictive for model-rotation scenarios where operators flush multiple models sequentially.

---

## Decision 8: Dependency addition

**Decision**: Add `redis[asyncio]` to portal-backend's requirements.

**Rationale**: portal-backend currently has no Redis dependency. This is the only new package needed. `redis[asyncio]` is the async variant of `redis-py`, consistent with the platform's httpx-async-everywhere standard. The Redis connection is created per-request (no persistent connection pool needed at this call frequency).

**Alternative**: Use `aioredis` — rejected, deprecated in favour of `redis[asyncio]` as of redis-py 4.2.
