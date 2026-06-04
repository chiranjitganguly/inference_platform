# Contract: Gateway Response Headers

**Feature**: 016-gateway-api-versioning | **Date**: 2026-06-03

This contract defines the response headers that the Kong gateway attaches to every response. Downstream integrations MAY read these headers; Kong is the sole authority for their values.

---

## Headers on Every Response

| Header | Source | Value | Notes |
|--------|--------|-------|-------|
| `X-Request-ID` | `correlation-id` plugin (global) | UUID v4 | Ties Kong span, LiteLLM span, Phoenix trace, Loki log entry |
| `X-Platform` | `response-transformer` plugin (global) | `inference-platform` | Identifies the platform for all consumers |
| `X-API-Version` | `response-transformer` plugin (per-service) | `1` or `2` | Integer; set at service scope, not global scope |

### `X-API-Version` values by route prefix

| Client request path prefix | `X-API-Version` value |
|----------------------------|-----------------------|
| `/v1/` | `1` |
| `/v2/` | `2` |
| `/health` | not set (health route is version-agnostic) |
| `/v1/spend` | `1` (portal-backend service) |
| Unknown prefix (e.g., `/v3/`) | absent — route returns 404 |

---

## Additional Headers on Deprecated Endpoints

Applied via route-level `response-transformer` plugin when an endpoint is scheduled for removal.

| Header | Format | Example | Notes |
|--------|--------|---------|-------|
| `Deprecation` | RFC 1123 HTTP-date | `Tue, 03 Jun 2026 00:00:00 GMT` | Date the endpoint was formally deprecated |
| `Sunset` | RFC 1123 HTTP-date | `Thu, 03 Dec 2026 00:00:00 GMT` | Date after which the endpoint will be removed (≥ 6 months after `Deprecation`) |
| `Link` | RFC 8288 link | `</docs/migration/foo>; rel="deprecation"` | Points to migration documentation or replacement endpoint |

**Enforcement**: The `Sunset` date must be at least 6 months after the `Deprecation` date. This is a mandatory operator constraint verified at PR review.

---

## Headers NOT set by the Gateway

Clients MUST NOT rely on these being present (they are stripped or absent):

| Header | Reason |
|--------|--------|
| `X-Request-ID` sent by client | Stripped by `pre-function` plugin before processing |
| `traceparent` sent by client | Stripped by `pre-function` plugin before processing |
| `X-API-Version` sent by upstream | Overwritten by Kong's service-level plugin |

---

## Error Response Shape

All error responses from Kong (auth failure, rate limit, size limit, 404) use the structured schema:

```json
{
  "error": "machine_readable_code",
  "message": "Human readable description",
  "detail": {}
}
```

Kong's native error responses (e.g., 401 from key-auth, 413 from request-size-limiting) follow this schema via Kong's error templates. The `X-Request-ID` and `X-Platform` headers are present even on error responses. `X-API-Version` is present on error responses for known version routes.

---

## Versioned Endpoint Surface

### v1 (stable — never broken)

| Method | Path | Auth required | Timeout |
|--------|------|--------------|---------|
| POST | `/v1/chat/completions` | Yes | 60 s |
| POST | `/v1/completions` | Yes | 60 s |
| GET | `/v1/models` | Yes | 60 s |
| POST | `/v1/embeddings` | Yes | 120 s |
| GET/POST/DELETE | `/v1/key` | LiteLLM master key | 30 s |
| GET | `/v1/spend` | Portal backend | 15 s |

### v2 (stub — breaking changes land here in future features)

| Method | Path | Auth required | Timeout | Notes |
|--------|------|--------------|---------|-------|
| POST | `/v2/chat/completions` | Yes | 60 s | Routes to same upstream as v1 |
| POST | `/v2/embeddings` | Yes | 120 s | Routes to same upstream as v1 |
| GET | `/v2/models` | Yes | 60 s | Routes to same upstream as v1 |

All v2 paths strip the `/v2` prefix and forward as `/v1/` paths to LiteLLM. Model name aliases are version-stable — `gpt-4o-mini` resolves identically on v1 and v2.
