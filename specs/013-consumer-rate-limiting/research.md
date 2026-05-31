# Research: Per-Consumer Gateway Rate Limiting

**Branch**: `013-consumer-rate-limiting` | **Date**: 2026-05-30

---

## §1 — Window Semantics: Fixed vs Sliding (SPEC CONFLICT RESOLVED)

**Decision**: Fixed/tumbling windows.

**Rationale**: The spec clarification session selected sliding/rolling windows, but Kong OSS 3.6's built-in `rate-limiting` plugin implements **fixed windows only** (calendar-aligned: second resets at :00.000, minute at :00, hour at :00:00). Sliding windows require the `rate-limiting-advanced` plugin, which is Kong Enterprise. The platform uses Kong 3.6 OSS (`kong:3.6` in `docker-compose.yml`). Introducing Enterprise is out of scope and violates the locked-version invariant (constitution §3).

**Alternatives considered**:
- `rate-limiting-advanced` (Enterprise) — sliding windows, but requires Enterprise license. Rejected.
- Custom Lua plugin implementing sliding window via Redis sorted sets — functional but adds maintenance burden. Rejected for this phase.
- Lua plugin with Redis sorted set (OSS) — viable future ADR if sliding-window fairness becomes a real operational concern.

**Spec update required**: `spec.md` FR-002, FR-004, the Key Entities / Rate-Limit Counter definition, and the Assumptions section must be updated to reflect fixed windows. `Retry-After` value = seconds to the next fixed window boundary (not time-to-oldest-request-aging-out).

**Practical impact**: Fixed windows can allow a brief burst at the window boundary (up to 2× the per-second limit for ~1 second straddling two windows). This is an accepted trade-off at 10 req/s — the maximum burst is 20 requests in a 1-second straddle. Operators should be aware.

---

## §2 — Kong `rate-limiting` Plugin Configuration

**Decision**: Global plugin, `limit_by=consumer`, `policy=redis`.

**Key parameters**:

| Parameter | Value | Notes |
|---|---|---|
| `config.second` | `10` | Default policy |
| `config.minute` | `300` | Default policy |
| `config.hour` | `10000` | Default policy |
| `config.policy` | `redis` | Shared counter store across Kong instances |
| `config.redis_host` | `redis` | Docker service name; resolves to the existing `redis:7.2-alpine` at port 6379 |
| `config.redis_port` | `6379` | Cache Redis (same instance as LiteLLM cache) |
| `config.limit_by` | `consumer` | Counter keyed on authenticated consumer username — not IP |
| `config.fault_tolerant` | `true` | Fail-open when Redis is unreachable (spec FR-012) |
| `config.hide_client_headers` | `false` | Expose `RateLimit-*` and `Retry-After` headers to callers (spec FR-006) |

**`limit_by=consumer` prerequisite**: The consumer must be identified by the time the rate-limiting plugin fires. The `key-auth` plugin installed in phase 012 runs before rate-limiting (Kong plugin priority: key-auth=1003, rate-limiting=901) — authenticated consumer context is always available.

**Unauthenticated requests**: Requests that fail `key-auth` are rejected with 401 before the rate-limiting plugin fires. There is no rate-limiting counter for unauthenticated traffic — this is correct behaviour (401 is the outer gate).

**Redis key namespace**: Kong rate-limiting writes keys in the form:
```
ratelimit:{consumer_username}:{identifier}:{window_start}:{period}
```
These keys are entirely distinct from LiteLLM's cache keys (which use `litellm_cache:*` namespace). No collision risk on the shared Redis instance.

---

## §3 — Response Headers

Kong's `rate-limiting` plugin (3.6) automatically adds these headers on every proxied response:

| Header | Meaning |
|---|---|
| `RateLimit-Limit-Second` | Max requests allowed per second window |
| `RateLimit-Remaining-Second` | Remaining requests in current second window |
| `RateLimit-Reset-Second` | Seconds until second window resets |
| `RateLimit-Limit-Minute` | Max requests allowed per minute window |
| `RateLimit-Remaining-Minute` | Remaining requests in current minute window |
| `RateLimit-Reset-Minute` | Seconds until minute window resets |
| `RateLimit-Limit-Hour` | Max requests allowed per hour window |
| `RateLimit-Remaining-Hour` | Remaining requests in current hour window |
| `RateLimit-Reset-Hour` | Seconds until hour window resets |
| `Retry-After` | Present on 429 only; seconds until the most restrictive exhausted window resets |

These satisfy spec FR-004 and FR-006. No custom header logic is needed.

**429 response body**: Kong's default 429 body is `{"message": "API rate limit exceeded"}`. This does not use the platform's structured error format `{"error","message","detail"}` (constitution §4.4). A `pre-function` Serverless plugin or a custom error template can override the 429 body. For the first implementation, the Kong default is acceptable — a follow-up ADR can standardise error bodies across all Kong-generated errors.

---

## §4 — Per-Consumer Prometheus Metrics

**Decision**: Enable Kong's built-in `prometheus` plugin with `per_consumer=true`.

Kong's Prometheus plugin (included in Kong 3.6 OSS) exposes metrics at `http://kong:8001/metrics`. With `per_consumer=true` it labels `kong_http_requests_total` by consumer:

```
kong_http_requests_total{service="litellm-inference", route="inference-v1", method="POST", status_code="429", consumer="smoke-test-consumer"}
```

**Deriving allowed vs rejected per consumer**:
- **Rejected**: `sum by (consumer) (rate(kong_http_requests_total{status_code="429"}[1m]))`
- **Allowed**: `sum by (consumer) (rate(kong_http_requests_total{status_code=~"2.."}[1m]))`

These satisfy spec FR-011 (per-consumer allowed/rejected counters). The metric names differ from the spec's `rate_limit_allowed_total` / `rate_limit_rejected_total` labels, but the same information is available via label filtering — functionally equivalent.

**Prometheus scrape target**: The existing `prometheus.yml` must include a scrape job for Kong's `/metrics` endpoint if not already present:
```yaml
- job_name: kong
  static_configs:
    - targets: ['kong:8001']
```

---

## §5 — Redis Unavailability & Alerting

**Decision**: `fault_tolerant=true` (fail-open) + Prometheus alert on Redis health.

Kong's `fault_tolerant=true` config causes rate-limit counter lookups to silently succeed when Redis is unreachable — requests flow through without rate-limit enforcement. This matches spec FR-012 and the constitution §5.2 rationale (a single enforcement mechanism failure should not bring down the platform).

**Alert**: Add a Prometheus alert rule that fires when Redis becomes unreachable. The existing `up` metric from Prometheus scraping covers this if a Redis exporter is present. Since the current stack does not include `redis_exporter`, the alert will be based on Kong's own error metric:

```promql
increase(kong_counter_total{name="redis_errors"}[1m]) > 0
```

If `kong_counter_total` is not available in Kong 3.6 OSS (metric names vary by version), fall back to alerting on Redis container health via a TCP probe or a `blackbox_exporter` scrape. The Prometheus rules file will include an inline comment documenting this dependency.

---

## §6 — `seed-kong.sh` Idempotency Pattern

All existing `seed-kong.sh` functions use the project's established `_plugin_exists_global` helper to check before creating. The new `create_rate_limiting_plugin()` function follows the same pattern:

```bash
create_rate_limiting_plugin() {
    info "Installing global rate-limiting plugin..."
    if ! _plugin_exists_global rate-limiting; then
        curl -sf -X POST "${KONG_ADMIN}/plugins" \
            -d "name=rate-limiting" \
            -d "config.second=10" \
            -d "config.minute=300" \
            -d "config.hour=10000" \
            -d "config.policy=redis" \
            -d "config.redis_host=redis" \
            -d "config.redis_port=6379" \
            -d "config.limit_by=consumer" \
            -d "config.fault_tolerant=true" \
            -d "config.hide_client_headers=false" \
            >/dev/null
    fi
    ok "Global plugin: rate-limiting (10/s, 300/min, 10000/hr — Redis, by consumer)"
}
```

The `prometheus` plugin (for per-consumer metrics) must also be added globally with `per_consumer=true` if not already present.
