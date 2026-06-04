# Data Model: Gateway API Versioning

**Branch**: `016-gateway-api-versioning` | **Date**: 2026-06-03

This feature has no application-layer data model (no database schema changes, no new PostgreSQL tables). The "data" is Kong declarative configuration. This document describes the Kong configuration entities introduced or modified.

---

## Kong Configuration Entities

### Modified Entity: Global `response-transformer` Plugin

**File**: `services/kong/kong.yml`

**Change**: Remove `X-API-Version: 1` from the `add.headers` list. Retain `X-Platform: inference-platform`.

**Before**:
```yaml
- name: response-transformer
  config:
    add:
      headers:
        - "X-Platform: inference-platform"
        - "X-API-Version: 1"
```

**After**:
```yaml
- name: response-transformer
  config:
    add:
      headers:
        - "X-Platform: inference-platform"
```

**Reason**: `X-API-Version` must differ per service (`1` for v1 services, `2` for v2 service). A global `add` would set `1` on all routes including v2, and a route/service-level `replace` cannot override a global `add` due to Kong's header_filter execution order.

---

### Modified Entity: `litellm` Service

**File**: `services/kong/kong.yml`

**Change**: Add a service-level `response-transformer` plugin that injects `X-API-Version: 1`.

```yaml
- name: litellm
  url: http://litellm:4000
  plugins:
    - name: response-transformer
      config:
        add:
          headers:
            - "X-API-Version: 1"
  routes:
    - name: litellm-proxy
      paths: [/v1]
      ...
    - name: models-catalogue
      paths: [/v1/models]
      ...
```

**Applies to routes**: `litellm-proxy` (`/v1`), `models-catalogue` (`/v1/models`)

---

### Modified Entity: `litellm-embeddings` Service

**File**: `services/kong/kong.yml`

**Change**: Add a service-level `response-transformer` plugin that injects `X-API-Version: 1`.

```yaml
- name: litellm-embeddings
  url: http://litellm:4000
  plugins:
    - name: response-transformer
      config:
        add:
          headers:
            - "X-API-Version: 1"
  routes:
    - name: litellm-embeddings-route
      paths: [/v1/embeddings]
      ...
```

---

### Modified Entity: `litellm-admin` Service

**File**: `services/kong/kong.yml`

**Change**: Add a service-level `response-transformer` plugin that injects `X-API-Version: 1`.

```yaml
- name: litellm-admin
  url: http://litellm:4000
  plugins:
    - name: response-transformer
      config:
        add:
          headers:
            - "X-API-Version: 1"
  routes:
    - name: litellm-key-manage
      paths: [/v1/key]
      ...
```

---

### New Entity: `litellm-v2` Service

**File**: `services/kong/kong.yml`

**Purpose**: Provides the v2 routing tier. Routes to the same LiteLLM upstream as v1. Strips the `/v2` prefix and rewrites to `/v1` before forwarding so LiteLLM receives its native `/v1/` path.

```yaml
- name: litellm-v2
  url: http://litellm:4000
  connect_timeout: 10000
  read_timeout:    60000
  write_timeout:   60000
  plugins:
    - name: response-transformer
      config:
        add:
          headers:
            - "X-API-Version: 2"
  routes:
    - name: v2-proxy
      paths: [/v2]
      methods: [GET, POST]
      strip_path: true        # strips /v2 prefix; combined with service url + path rewrite
      plugins:
        - name: key-auth
          config:
            key_names: [Authorization]
            key_in_header: true
            hide_credentials: true
```

**Path rewrite note**: Kong's `strip_path: true` strips the matched prefix (`/v2`), then appends the remainder to the service URL. So `/v2/chat/completions` → strips `/v2` → appends `/chat/completions` to `http://litellm:4000` → LiteLLM receives `GET/POST http://litellm:4000/chat/completions`. LiteLLM's OpenAI-compatible endpoint is `/v1/chat/completions`, so the service URL must include the `/v1` base: `url: http://litellm:4000/v1`. This means the `litellm-v2` service URL must be `http://litellm:4000/v1` (not just `http://litellm:4000`).

**Corrected service URL**:
```yaml
- name: litellm-v2
  url: http://litellm:4000/v1      # /v2/chat/completions → /v1/chat/completions
  ...
  routes:
    - name: v2-proxy
      paths: [/v2]
      strip_path: true             # /v2/chat/completions → strip /v2 → /chat/completions → litellm:4000/v1/chat/completions ✓
```

---

### New Entity: Deprecated Route Plugin (future use)

**File**: `services/kong/kong.yml`

**Purpose**: Route-level `response-transformer` plugin added to any route scheduled for deprecation. Not applied to any route in this feature's initial delivery — provided as the documented pattern for operators.

```yaml
# Example: deprecating /v1/completions (legacy text completions)
- name: response-transformer
  config:
    add:
      headers:
        - "Deprecation: Tue, 03 Jun 2026 00:00:00 GMT"          # announcement date
        - "Sunset: Wed, 03 Dec 2026 00:00:00 GMT"               # removal date (≥6 months)
        - 'Link: </docs/migration/v1-completions>; rel="deprecation"'
```

**Constraint**: `Sunset` date MUST be at least 6 months after `Deprecation` date. Validated by PR review and optionally by a CI lint script.

---

## Validation Rules

| Rule | Constraint |
|------|-----------|
| `X-API-Version` on v1 routes | Must equal `1` |
| `X-API-Version` on v2 routes | Must equal `2` |
| `X-API-Version` absent | On unknown-prefix routes (natural 404) |
| `Sunset` date | Must be ≥ 6 months after `Deprecation` date (operator responsibility) |
| `Link` header on deprecated routes | Must include `rel="deprecation"` |
| v2 service URL | Must include `/v1` base path so LiteLLM receives OpenAI-compatible paths |

---

## State Transitions

Route lifecycle:

```
active → deprecated (Deprecation + Sunset + Link headers added)
       → sunset (past Sunset date: route removed or returns 410)
```

Version lifecycle:

```
v1: active (stable — never removed without major platform version bump)
v2: active (stub — breaking changes added here over time)
```
