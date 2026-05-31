# Data Model: Per-Consumer Gateway Rate Limiting

**Branch**: `013-consumer-rate-limiting` | **Date**: 2026-05-30

---

## Entities

### Rate-Limit Policy (Kong Plugin Config)

Stored as a Kong plugin object in the Kong PostgreSQL database. One global instance; no per-consumer overrides in this phase.

| Field | Type | Value | Notes |
|---|---|---|---|
| `name` | string | `rate-limiting` | Kong plugin identifier |
| `enabled` | bool | `true` | |
| `config.second` | integer | `10` | Default per-second limit |
| `config.minute` | integer | `300` | Default per-minute limit |
| `config.hour` | integer | `10000` | Default per-hour limit |
| `config.policy` | enum | `redis` | Counter storage backend |
| `config.redis_host` | string | `redis` | Docker service name |
| `config.redis_port` | integer | `6379` | Shared Redis instance |
| `config.limit_by` | enum | `consumer` | Counter keying: authenticated consumer username |
| `config.fault_tolerant` | bool | `true` | Fail open when Redis is unreachable |
| `config.hide_client_headers` | bool | `false` | Expose quota headers to callers |
| `service` | null | `null` | Global scope — applies to all services |
| `route` | null | `null` | Global scope — applies to all routes |
| `consumer` | null | `null` | Global scope — applies to all consumers |

---

### Consumer (existing — Kong DB)

Established by phase 012. Rate-limiting reads the authenticated consumer username from the Kong request context (populated by the `key-auth` plugin). No new fields added to the consumer entity.

| Field | Type | Notes |
|---|---|---|
| `username` | string | Primary key for rate-limit counter lookup |
| `tags` | string[] | Informational; not used by rate-limiting plugin |

---

### Rate-Limit Counter (Redis)

One entry per `(consumer, window_type, window_start)` triple. Written and read atomically by Kong's rate-limiting plugin via Lua Redis scripts.

**Key format**:
```
ratelimit:<consumer_username>:<period>:<window_start_epoch>
```

**Example keys** for consumer `smoke-test-consumer` at 2026-05-30 14:00:00 UTC:
```
ratelimit:smoke-test-consumer:second:1748613600
ratelimit:smoke-test-consumer:minute:1748613600
ratelimit:smoke-test-consumer:hour:1748613600
```

| Field | Type | Notes |
|---|---|---|
| value | integer | Count of requests in this window |
| TTL | seconds | Set by Kong to the window duration (1s / 60s / 3600s) |

Counter values expire automatically; no manual cleanup required.

---

## Response Header Schema

### Successful Request (2xx)

All three window types are included on every proxied response:

```
RateLimit-Limit-Second: 10
RateLimit-Remaining-Second: 7
RateLimit-Reset-Second: 1

RateLimit-Limit-Minute: 300
RateLimit-Remaining-Minute: 287
RateLimit-Reset-Minute: 43

RateLimit-Limit-Hour: 10000
RateLimit-Remaining-Hour: 9987
RateLimit-Reset-Hour: 2143
```

### Throttle Response (429)

Same quota headers plus `Retry-After`:

```
HTTP/1.1 429 Too Many Requests
RateLimit-Limit-Second: 10
RateLimit-Remaining-Second: 0
RateLimit-Reset-Second: 1
Retry-After: 1

Content-Type: application/json
{"message": "API rate limit exceeded"}
```

`Retry-After` is the reset time of the **most restrictive exhausted window** (the one with the longest remaining time to reset). If per-second and per-hour are both exhausted, `Retry-After` reflects the hour window reset.

---

## Window Semantics Note

Windows are **fixed/tumbling** (calendar-aligned). This is a Kong OSS constraint — see `research.md §1` for full rationale. The spec's sliding-window clarification has been superseded by this implementation constraint.

| Window | Resets at |
|---|---|
| second | top of each UTC second |
| minute | top of each UTC minute (:00) |
| hour | top of each UTC hour (:00:00) |

The per-second window allows a worst-case burst of up to 20 requests across a 1-second boundary straddle (10 at end of second N, 10 at start of second N+1). This is an accepted trade-off at this scale.

---

## State Transitions

```
Request arrives at Kong
       │
       ▼
key-auth plugin (priority 1003)
       │
   ┌───┴────────────────┐
   │ 401 Unauthorized   │ ← no valid API key
   └────────────────────┘
       │ authenticated consumer identified
       ▼
rate-limiting plugin (priority 901)
       │
   ┌───┴──────────────────────────────────────────────────┐
   │                                                      │
counter < limit                               counter >= limit
   │                                                      │
   ▼                                                      ▼
increment counter                             return 429 + Retry-After
pass request downstream                       (no downstream call)
add RateLimit-* headers to response
```

If Redis is unreachable → `fault_tolerant=true` → skip counter check → pass request downstream (fail-open).
