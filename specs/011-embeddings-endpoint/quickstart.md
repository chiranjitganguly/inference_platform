# Quickstart: Embeddings Endpoint (011)

**Prerequisites**: `make up-core && make seed-kong` completed. `SMOKE_API_KEY` set in `.env`.

---

## Verify: Single Embedding

```bash
curl -s -X POST http://localhost:8080/v1/embeddings \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"text-embedding-3-small","input":"Hello world"}' \
  | jq '{model:.model, dims:(.data[0].embedding|length), tokens:.usage.total_tokens}'
```

**Expected output**:
```json
{
  "model": "text-embedding-3-small",
  "dims": 1536,
  "tokens": 2
}
```

---

## Verify: Large Model Dimensions

```bash
curl -s -X POST http://localhost:8080/v1/embeddings \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"text-embedding-3-large","input":"Hello world"}' \
  | jq '.data[0].embedding|length'
```

**Expected output**: `3072`

---

## Verify: Chat Model Rejected

```bash
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8080/v1/embeddings \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","input":"Hello world"}'
```

**Expected output**: `400`

---

## Verify: Batch Embeddings

```bash
curl -s -X POST http://localhost:8080/v1/embeddings \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"text-embedding-3-small","input":["first","second","third"]}' \
  | jq '{count:(.data|length), dims_0:(.data[0].embedding|length)}'
```

**Expected output**:
```json
{
  "count": 3,
  "dims_0": 1536
}
```

---

## Verify: No Caching (Two Upstream Calls)

Send the same request twice, then check LiteLLM spend logs:

```bash
for i in 1 2; do
  curl -s -X POST http://localhost:8080/v1/embeddings \
    -H "Authorization: Bearer ${SMOKE_API_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"model":"text-embedding-3-small","input":"cache test"}' > /dev/null
done

# Check spend log via LiteLLM admin API
curl -s http://localhost:8080/v1/spend/logs \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
  | jq '[.[] | select(.model == "text-embedding-3-small")] | length'
```

**Expected output**: `2` (two separate spend log entries — not served from cache)

---

## Verify: Prometheus Metrics

```bash
curl -s http://localhost:9090/api/v1/query \
  --data-urlencode 'query=litellm_request_total{model="text-embedding-3-small"}' \
  | jq '.data.result[0].value[1]'
```

**Expected**: A non-zero string value after running the smoke tests above.

---

## Memory Check

```bash
make stats
```

This feature adds no new services. Memory delta should be ~0 MB above baseline.
