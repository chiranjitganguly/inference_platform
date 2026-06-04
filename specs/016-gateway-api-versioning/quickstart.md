# Quickstart: Gateway API Versioning

**Branch**: `016-gateway-api-versioning` | **Date**: 2026-06-03

## What changes

One file: `services/kong/kong.yml`

1. Global `response-transformer` plugin: remove `X-API-Version: 1` (move to service scope)
2. Services `litellm`, `litellm-embeddings`, `litellm-admin`: add service-level `response-transformer` with `X-API-Version: 1`
3. New service `litellm-v2` with URL `http://litellm:4000/v1`, routes `/v2`, and service-level `response-transformer` with `X-API-Version: 2`

## Apply the change

```bash
make up-core          # ensure Kong is running
make seed-kong        # reload declarative config after editing kong.yml
```

Or if Kong is already up and you are iterating:
```bash
make restart svc=kong
```

## Verify v1 (regression check)

```bash
# X-API-Version must be 1
curl -si -H "Authorization: Bearer $SMOKE_API_KEY" \
  http://localhost:8080/v1/models \
  | grep -i "X-API-Version"
# Expected: X-API-Version: 1

curl -si -H "Authorization: Bearer $SMOKE_API_KEY" \
  http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}]}' \
  | grep -i "X-API-Version"
# Expected: X-API-Version: 1
```

## Verify v2

```bash
# X-API-Version must be 2
curl -si -H "Authorization: Bearer $SMOKE_API_KEY" \
  http://localhost:8080/v2/models \
  | grep -i "X-API-Version"
# Expected: X-API-Version: 2

curl -si -H "Authorization: Bearer $SMOKE_API_KEY" \
  http://localhost:8080/v2/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}]}' \
  | grep -i "X-API-Version"
# Expected: X-API-Version: 2

# Model alias stability: same model name resolves on v2
curl -si -H "Authorization: Bearer $SMOKE_API_KEY" \
  http://localhost:8080/v2/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}]}' \
  | grep -i "HTTP/"
# Expected: HTTP/1.1 200 OK (not 404 — model alias resolved)
```

## Verify unknown version returns 404 with no X-API-Version

```bash
curl -si http://localhost:8080/v3/chat/completions | head -5
# Expected: HTTP/1.1 404
# Must NOT contain: X-API-Version
```

## Verify X-Platform present on all responses

```bash
curl -si -H "Authorization: Bearer $SMOKE_API_KEY" \
  http://localhost:8080/v2/models | grep -i "X-Platform"
# Expected: X-Platform: inference-platform
```

## How to add a deprecation notice to an existing route

Add a route-level `response-transformer` plugin to the target route in `kong.yml`:

```yaml
routes:
  - name: some-deprecated-route
    paths: [/v1/completions]
    plugins:
      - name: response-transformer
        config:
          add:
            headers:
              - "Deprecation: Tue, 03 Jun 2026 00:00:00 GMT"
              - "Sunset: Thu, 03 Dec 2026 00:00:00 GMT"
              - 'Link: </docs/migration/completions>; rel="deprecation"'
```

**Rule**: `Sunset` MUST be at least 6 months after `Deprecation`. Enforce at PR review.

Verify the headers appear:
```bash
curl -si -H "Authorization: Bearer $SMOKE_API_KEY" \
  http://localhost:8080/v1/completions | grep -iE "Deprecation|Sunset|Link"
# Expected: all three headers present with correct dates
```

## Smoke test integration

Add to `scripts/smoke-test.sh` (alongside existing v1 checks):

```bash
# --- Feature 016: API versioning ---
assert_header "v1 version header" \
  "$(curl -si -H "Authorization: Bearer $SMOKE_API_KEY" http://localhost:8080/v1/models)" \
  "X-API-Version" "1"

assert_header "v2 version header" \
  "$(curl -si -H "Authorization: Bearer $SMOKE_API_KEY" http://localhost:8080/v2/models)" \
  "X-API-Version" "2"

assert_status "unknown version 404" \
  "$(curl -si http://localhost:8080/v3/models)" \
  "404"
```
