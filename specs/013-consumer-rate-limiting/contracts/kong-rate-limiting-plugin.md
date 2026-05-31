# Contract: Kong Rate-Limiting Plugin (Admin API)

**Interface**: Kong Admin API — Global Plugin Registration
**Delivered by**: `scripts/seed-kong.sh` → `create_rate_limiting_plugin()`

---

## Install: Global Rate-Limiting Plugin

**Endpoint**: `POST /plugins`
**Admin API**: `http://localhost:8001` (localhost-only)

```bash
curl -sf -X POST http://localhost:8001/plugins \
  -d "name=rate-limiting" \
  -d "config.second=10" \
  -d "config.minute=300" \
  -d "config.hour=10000" \
  -d "config.policy=redis" \
  -d "config.redis_host=redis" \
  -d "config.redis_port=6379" \
  -d "config.limit_by=consumer" \
  -d "config.fault_tolerant=true" \
  -d "config.hide_client_headers=false"
```

**Expected response** (201 Created):
```json
{
  "id": "<uuid>",
  "name": "rate-limiting",
  "enabled": true,
  "service": null,
  "route": null,
  "consumer": null,
  "config": {
    "second": 10,
    "minute": 300,
    "hour": 10000,
    "policy": "redis",
    "redis_host": "redis",
    "redis_port": 6379,
    "limit_by": "consumer",
    "fault_tolerant": true,
    "hide_client_headers": false
  }
}
```

**Idempotency**: `seed-kong.sh` guards this call with `_plugin_exists_global rate-limiting` — if the plugin already exists, the POST is skipped.

---

## Install: Prometheus Plugin (per-consumer metrics)

**Endpoint**: `POST /plugins`

```bash
curl -sf -X POST http://localhost:8001/plugins \
  -d "name=prometheus" \
  -d "config.per_consumer=true"
```

**Expected response** (201 Created):
```json
{
  "id": "<uuid>",
  "name": "prometheus",
  "enabled": true,
  "config": {
    "per_consumer": true
  }
}
```

**Idempotency**: Guarded by `_plugin_exists_global prometheus`.

---

## Verify: Plugin Registered

```bash
curl -sf http://localhost:8001/plugins?name=rate-limiting | python3 -c \
  "import sys, json; d=json.load(sys.stdin); print('FOUND' if d['data'] else 'MISSING')"
```

Expected output: `FOUND`

---

## Verify: Plugin is Enforcing

**Burst test** — send 12 requests in quick succession, expect the last 2 to return 429:

```bash
for i in $(seq 1 12); do
  status=$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: ${SMOKE_API_KEY}" \
    http://localhost:8080/v1/models)
  echo "Request ${i}: HTTP ${status}"
done
```

Expected output: requests 1–10 return `200`, requests 11–12 return `429`.

**Retry-After header present** on 429:

```bash
curl -si -H "Authorization: ${SMOKE_API_KEY}" http://localhost:8080/v1/models | grep -i retry-after
```

Expected: `Retry-After: 1` (or similar, ≥1)

**Quota headers present** on 200:

```bash
curl -si -H "Authorization: ${SMOKE_API_KEY}" http://localhost:8080/v1/models | grep -i ratelimit
```

Expected: `RateLimit-Limit-Second: 10`, `RateLimit-Remaining-Second: <N>`, etc.

---

## Verify: Consumer Isolation

```bash
# Consumer A hammers past limit
for i in $(seq 1 15); do
  curl -s -o /dev/null -w '%{http_code}\n' \
    -H "Authorization: ${SMOKE_API_KEY}" http://localhost:8080/v1/models
done &

# Consumer B (different key) should still get 200
sleep 0.1
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: ${CONSUMER_B_API_KEY}" http://localhost:8080/v1/models
```

Expected: Consumer B's request returns `200` while Consumer A's later requests return `429`.

---

## Prometheus Metrics Contract

After the Prometheus plugin is installed and at least one request has been made:

```bash
curl -s http://localhost:8001/metrics | grep kong_http_requests_total
```

Expected: lines including `consumer="smoke-test-consumer"` label.

**Rate-limit rejected query** (PromQL):
```promql
sum by (consumer) (rate(kong_http_requests_total{status_code="429"}[1m]))
```

**Rate-limit allowed query** (PromQL):
```promql
sum by (consumer) (rate(kong_http_requests_total{status_code=~"2.."}[1m]))
```
