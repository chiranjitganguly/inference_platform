# Quickstart: Gateway Request Body Size Limit

**Feature**: 015-gateway-body-size-limit

---

## Prerequisites

- `make up-core` has completed and all core services are healthy.
- `make seed-kong` has been run (or re-run after this feature's changes are applied).
- `SMOKE_API_KEY` is set in your `.env`.

---

## Apply the Feature

```bash
# 1. Pull the branch
git checkout 015-gateway-body-size-limit

# 2. Re-seed Kong (installs the request-size-limiting plugin)
make seed-kong

# 3. Verify the plugin is registered
curl -s http://localhost:8001/plugins?name=request-size-limiting | python3 -m json.tool
# → Expect: "data" array with one entry, config.allowed_payload_size = 10
```

---

## Acceptance Tests

### Test 1 — Oversized payload returns 413 (SC-001)

```bash
# Generate an 11 MB payload and POST it to the inference route
python3 -c "import json; print(json.dumps({'model':'gpt-4o','messages':[{'role':'user','content':'x'*11534336}]}))" \
  | curl -s -o /dev/null -w "%{http_code}" \
    -X POST http://localhost:8080/v1/chat/completions \
    -H "Authorization: ${SMOKE_API_KEY}" \
    -H "Content-Type: application/json" \
    --data-binary @-
# Expected: 413
```

### Test 2 — Boundary: exactly 10 MB is allowed (SC-002)

```bash
python3 -c "import json; print(json.dumps({'model':'gpt-4o','messages':[{'role':'user','content':'x'*10485760}]}))" \
  | curl -s -o /dev/null -w "%{http_code}" \
    -X POST http://localhost:8080/v1/chat/completions \
    -H "Authorization: ${SMOKE_API_KEY}" \
    -H "Content-Type: application/json" \
    --data-binary @-
# Expected: 200 or 400 (model error) — NOT 413
```

### Test 3 — Normal request unaffected (SC-002)

```bash
curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"hello"}]}'
# Expected: 200
```

### Test 4 — Embeddings route also enforces limit (SC-004)

```bash
python3 -c "import json; print(json.dumps({'model':'text-embedding-3-small','input':'x'*11534336}))" \
  | curl -s -o /dev/null -w "%{http_code}" \
    -X POST http://localhost:8080/v1/embeddings \
    -H "Authorization: ${SMOKE_API_KEY}" \
    -H "Content-Type: application/json" \
    --data-binary @-
# Expected: 413
```

### Test 5 — 413 response carries X-Request-ID (observability)

```bash
python3 -c "print('x'*11534336)" \
  | curl -s -D - -o /dev/null \
    -X POST http://localhost:8080/v1/chat/completions \
    -H "Authorization: ${SMOKE_API_KEY}" \
    -H "Content-Type: application/json" \
    --data-binary @- \
  | grep -E "HTTP|X-Request-ID|X-Platform"
# Expected: HTTP/1.1 413, X-Request-ID: <uuid>, X-Platform: inference-platform
```

### Test 6 — Rejection audit log visible in Loki (SC-005)

```bash
# Query Loki for 413 events (requires obs profile running)
curl -s "http://localhost:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={service="kong"} | json | status="413"' \
  --data-urlencode 'limit=5' \
  | python3 -m json.tool | grep -E '"status"|"request_id"|"path"'
# Expected: entries with status=413, a non-empty request_id, and the inference path
```

---

## Reconfigure the Limit (FR-006)

The limit is set in `scripts/seed-kong.sh` (`create_request_size_plugin` function) and mirrored in `services/kong/kong.yml`. To change it:

1. Update the value in both files.
2. Re-run `make seed-kong` — the script patches the existing plugin via `PATCH /plugins/{id}` if it already exists.
3. No container restart required.

---

## Memory Impact

This feature adds zero new containers. `make stats` after seeding should show no change in the memory profile.
