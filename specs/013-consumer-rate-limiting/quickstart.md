# Quickstart: Per-Consumer Gateway Rate Limiting

**Prerequisites**: `make up-core` has been run and `make seed-kong` has been re-run after this feature is implemented.

---

## 1. Re-seed Kong

The rate-limiting and Prometheus plugins are installed by `seed-kong.sh`. Re-run it after pulling this feature:

```bash
make seed-kong
```

Expected output includes:
```
[OK]      Global plugin: rate-limiting (10/s, 300/min, 10000/hr — Redis, by consumer)
[OK]      Global plugin: prometheus (per-consumer metrics)
```

---

## 2. Confirm Plugin Is Active

```bash
curl -sf http://localhost:8001/plugins?name=rate-limiting \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['config'])"
```

Expected: dict showing `second=10, minute=300, hour=10000, policy=redis, limit_by=consumer`.

---

## 3. Test Rate Limiting (burst to limit)

Send 12 requests to the same authenticated endpoint in quick succession:

```bash
for i in $(seq 1 12); do
  http_code=$(curl -s -o /dev/null -w '%{http_code}' \
    --max-time 2 \
    -H "Authorization: ${SMOKE_API_KEY}" \
    http://localhost:8080/v1/models)
  printf 'Request %2d → HTTP %s\n' "$i" "$http_code"
done
```

Expected result:
```
Request  1 → HTTP 200
...
Request 10 → HTTP 200
Request 11 → HTTP 429
Request 12 → HTTP 429
```

---

## 4. Inspect Throttle Response Headers

```bash
curl -si -H "Authorization: ${SMOKE_API_KEY}" http://localhost:8080/v1/models \
  | grep -iE 'ratelimit|retry-after'
```

Expected headers on a 200 response:
```
ratelimit-limit-second: 10
ratelimit-remaining-second: 9
ratelimit-reset-second: 1
ratelimit-limit-minute: 300
ratelimit-remaining-minute: 299
ratelimit-reset-minute: 57
ratelimit-limit-hour: 10000
ratelimit-remaining-hour: 9999
ratelimit-reset-hour: 3543
```

On a 429 response, `retry-after: 1` (or higher for minute/hour exhaustion) is also present.

---

## 5. Verify Consumer Isolation

Requires two distinct API keys. If only `SMOKE_API_KEY` exists, create a second consumer first:

```bash
# Create a second test consumer (one-time)
curl -sf -X PUT http://localhost:8001/consumers/consumer-b \
  -d "username=consumer-b"
curl -sf -X POST http://localhost:8001/consumers/consumer-b/key-auth \
  -d "key=consumer-b-test-key"

export CONSUMER_B_KEY=consumer-b-test-key
```

Then run the isolation test:

```bash
# Hammer Consumer A past limit in background
for i in $(seq 1 20); do
  curl -s -o /dev/null -H "Authorization: ${SMOKE_API_KEY}" http://localhost:8080/v1/models
done &

# Consumer B should still succeed
sleep 0.05
result=$(curl -s -o /dev/null -w '%{http_code}' \
  -H "Authorization: ${CONSUMER_B_KEY}" http://localhost:8080/v1/models)
echo "Consumer B result: HTTP ${result}"
```

Expected: `Consumer B result: HTTP 200`

---

## 6. Verify Prometheus Metrics

```bash
curl -s http://localhost:8001/metrics \
  | grep 'kong_http_requests_total' \
  | grep 'consumer="smoke-test-consumer"' \
  | head -5
```

Expected: metric lines with `status_code="200"` and `status_code="429"` labelled with `consumer="smoke-test-consumer"`.

---

## 7. Run Full Smoke Test

```bash
make smoke
```

All existing probes should still pass. The new rate-limit probe (burst test) should also pass once `smoke-test.sh` is updated.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| All requests return 429 immediately | `limit_by` fell back to IP and your IP is being blocked | Verify `key-auth` plugin is running and consumer is authenticated |
| 429 has no `Retry-After` header | `hide_client_headers=true` | Re-seed with `false` |
| Counter does not reset after window | Redis not reachable | `docker compose logs redis` — confirm Redis is up |
| Consumer B gets 429 when Consumer A is throttled | Plugin scoped to consumer but `limit_by=ip` | Check plugin config via `curl http://localhost:8001/plugins?name=rate-limiting` |
| Prometheus metrics missing consumer label | `per_consumer=false` on Prometheus plugin | Re-seed; check plugin config |
