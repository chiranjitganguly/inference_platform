# Quickstart: API Gateway Authentication

**Feature**: 012-kong-api-gateway-auth
**Date**: 2026-05-30

Developer guide for implementing and validating this feature.

---

## What changes

| File | Change |
|---|---|
| `docker-compose.yml` | Add `kong-migration` service; change Kong from DB-less to DB-backed (postgres) |
| `scripts/seed-kong.sh` | Full rewrite: Admin API seeding (services, routes, consumers, plugins) |
| `scripts/smoke-test.sh` | Add 401-rejection assertions for unauthenticated requests |
| `services/kong/kong.yml` | No longer loaded at runtime; kept as historical reference |
| `.env.example` | Ensure `KONG_PG_PASSWORD`, `KONG_PG_USER`, `KONG_PG_DATABASE` are listed |

---

## docker-compose.yml changes

### kong-migration service (add to `core` profile)

```yaml
kong-migration:
  image: kong:3.6
  profiles: [core]
  environment:
    KONG_DATABASE:    postgres
    KONG_PG_HOST:     postgres
    KONG_PG_PORT:     "5432"
    KONG_PG_DATABASE: ${KONG_PG_DATABASE:-kong}
    KONG_PG_USER:     ${KONG_PG_USER:-kong}
    KONG_PG_PASSWORD: ${KONG_PG_PASSWORD}
  command: >
    sh -c "kong migrations bootstrap 2>/dev/null || true && kong migrations up"
  depends_on:
    postgres:
      condition: service_healthy
  restart: on-failure
```

### kong service (modify)

Remove:
```yaml
KONG_DATABASE:            off
KONG_DECLARATIVE_CONFIG:  /etc/kong/declarative/kong.yml
```

Add:
```yaml
KONG_DATABASE:    postgres
KONG_PG_HOST:     postgres
KONG_PG_PORT:     "5432"
KONG_PG_DATABASE: ${KONG_PG_DATABASE:-kong}
KONG_PG_USER:     ${KONG_PG_USER:-kong}
KONG_PG_PASSWORD: ${KONG_PG_PASSWORD}
```

Add dependency on migration:
```yaml
depends_on:
  postgres:
    condition: service_healthy
  kong-migration:
    condition: service_completed_successfully
```

Remove volume mount for kong.yml declarative config.

---

## seed-kong.sh rewrite

The rewritten script uses the Kong Admin API (`:8001`) to create all entities idempotently. Key sections:

### 1. Wait for Admin API
```bash
wait_for_kong()  # poll GET /status until 200
```

### 2. Create consumers
```bash
# anonymous consumer (no credentials — used for health route passthrough)
curl -sf -X PUT http://localhost:8001/consumers/anonymous \
  -d username=anonymous

# smoke-test consumer
curl -sf -X PUT http://localhost:8001/consumers/smoke-test-consumer \
  -d username=smoke-test-consumer \
  -d "tags[]=smoke-test"
```

### 3. Add key credential to smoke-test consumer
```bash
curl -sf -X POST http://localhost:8001/consumers/smoke-test-consumer/key-auth \
  -d "key=${SMOKE_API_KEY}"
```

### 4. Create services (example — inference)
```bash
curl -sf -X PUT http://localhost:8001/services/litellm-inference \
  -d "url=http://litellm:4000" \
  -d "connect_timeout=10000" \
  -d "read_timeout=60000" \
  -d "write_timeout=60000"
```

### 5. Create routes (example — /v1)
```bash
curl -sf -X PUT http://localhost:8001/services/litellm-inference/routes/inference-v1 \
  -d "paths[]=/v1" \
  -d "methods[]=GET" \
  -d "methods[]=POST" \
  -d "strip_path=false"
```

### 6. Global plugins
```bash
# key-auth — global, Authorization header, anonymous consumer UUID
ANON_UUID=$(curl -sf http://localhost:8001/consumers/anonymous | jq -r '.id')

curl -sf -X POST http://localhost:8001/plugins \
  -d "name=key-auth" \
  -d "config.key_names[]=Authorization" \
  -d "config.hide_credentials=true" \
  -d "config.anonymous=${ANON_UUID}"

# acl — global deny anonymous
curl -sf -X POST http://localhost:8001/plugins \
  -d "name=acl" \
  -d "config.deny[]=anonymous"

# correlation-id
curl -sf -X POST http://localhost:8001/plugins \
  -d "name=correlation-id" \
  -d "config.header_name=X-Request-ID" \
  -d "config.generator=uuid" \
  -d "config.echo_downstream=true"

# response-transformer
curl -sf -X POST http://localhost:8001/plugins \
  -d "name=response-transformer" \
  -d "config.add.headers[]=X-Platform:inference-platform" \
  -d "config.add.headers[]=X-API-Version:1"
```

### 7. Add smoke-test consumer to api-consumers ACL group
```bash
curl -sf -X POST http://localhost:8001/consumers/smoke-test-consumer/acls \
  -d "group=api-consumers"

curl -sf -X POST http://localhost:8001/consumers/anonymous/acls \
  -d "group=anonymous"
```

### 8. Route-level ACL override for /health
```bash
HEALTH_ROUTE_ID=$(curl -sf http://localhost:8001/routes/health | jq -r '.id')

curl -sf -X POST http://localhost:8001/routes/${HEALTH_ROUTE_ID}/plugins \
  -d "name=acl" \
  -d "config.allow[]=anonymous" \
  -d "config.allow[]=api-consumers"
```

---

## Implementation note: 401 vs 403 for missing keys

With `anonymous` configured on the global key-auth plugin, a request with a **missing key** is tagged as the anonymous consumer. The ACL plugin then returns **403** (forbidden), not 401. A request with an **invalid/unrecognised key** returns **401** from key-auth before reaching ACL.

The spec requires HTTP 401 for rejected inference requests. Two options:

**Option A** (recommended): Remove `anonymous` from the global key-auth config. The health route instead uses a **separate service and route** with NO key-auth plugin. The `/health` path is handled by a dedicated `litellm-health` service that has no auth plugin at all.

**Option B**: Accept 403 for missing-key rejections (common in API gateway implementations) and update spec SC-001 wording to "HTTP 401 or 403". Simplest change.

**Recommended**: Option A. It avoids the anonymous consumer complexity entirely. Global key-auth with no `anonymous` config returns 401 for all unauthenticated inference requests. Health service has no key-auth plugin, so unauthenticated health requests pass freely. This is structurally identical to the current DB-less configuration.

Revisit the data-model.md ACL plugin design if Option A is chosen during implementation — the `acl` and `anonymous consumer` entities are not needed.

---

## Running after implementation

```bash
# Start core stack (includes kong-migration)
make up-core

# Wait for postgres + migration + kong to be healthy, then seed
make seed-kong

# Verify
make smoke

# Check memory budget
make stats
```

---

## Smoke test additions

Add to `scripts/smoke-test.sh`:

```bash
# Auth rejection on inference endpoints
for path in /v1/models /v1/chat/completions /v1/embeddings; do
  status=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "http://localhost:8080${path}" \
    -H "Content-Type: application/json" \
    -d '{}')
  [ "$status" = "401" ] || fail "Expected 401 on ${path} without key, got ${status}"
done

# Health accessible without key
status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/health)
[ "$status" = "200" ] || fail "Expected 200 on /health without key, got ${status}"

# Direct internal port must be unreachable
curl -sf --connect-timeout 2 http://localhost:4000/v1/models \
  && fail "LiteLLM port 4000 is externally reachable — constitution violation" \
  || ok "LiteLLM port 4000 is internal-only"
```
