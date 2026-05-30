# Data Model: API Gateway Authentication

**Feature**: 012-kong-api-gateway-auth
**Date**: 2026-05-30

Kong DB-backed mode stores all gateway configuration in the `kong` PostgreSQL database. The entities below are the canonical objects created by `seed-kong.sh` via the Kong Admin API.

---

## Entities

### Consumer

Represents a named client identity. API keys are bound to a Consumer.

| Field | Type | Constraints | Notes |
|---|---|---|---|
| `username` | string | unique, required | Human-readable name: `smoke-test-consumer`, `anonymous` |
| `custom_id` | string | optional | Not used in this feature |
| `tags` | string[] | optional | e.g. `["smoke-test"]`, `["internal"]` |

**Consumers provisioned by seed-kong.sh**:
- `smoke-test-consumer` — the CI/CD validation identity; holds one `key-auth` credential
- `anonymous` — a credential-free consumer; used as the key-auth `anonymous` fallback for unauthenticated requests reaching the health route

---

### KeyAuthCredential

A raw API key bound to a Consumer. Presented in the `Authorization` header.

| Field | Type | Constraints | Notes |
|---|---|---|---|
| `consumer` (FK) | Consumer | required | The consumer this key belongs to |
| `key` | string | unique | Raw key value; sourced from `SMOKE_API_KEY` env var |
| `tags` | string[] | optional | |

**Note**: Keys are sent as `Authorization: <raw-key>` — no `Bearer ` prefix. The `hide_credentials: true` plugin config strips the header before forwarding to upstream.

---

### Service

An upstream backend registered in Kong.

| Field | Type | Notes |
|---|---|---|
| `name` | string | Unique name, e.g. `litellm-inference` |
| `url` | string | Upstream URL: `http://litellm:4000`, `http://portal-backend:8092` |
| `connect_timeout` | int (ms) | Default 10 000 |
| `read_timeout` | int (ms) | Varies by service (see timeouts table below) |
| `write_timeout` | int (ms) | Matches `read_timeout` |

**Services provisioned**:

| Name | URL | Read Timeout |
|---|---|---|
| `litellm-inference` | `http://litellm:4000` | 60 000 ms |
| `litellm-embeddings` | `http://litellm:4000` | 120 000 ms |
| `litellm-health` | `http://litellm:4000` | 10 000 ms |
| `litellm-admin` | `http://litellm:4000` | 30 000 ms |
| `portal-backend` | `http://portal-backend:8092` | 15 000 ms |

---

### Route

Maps an incoming request path to a Service.

| Field | Type | Notes |
|---|---|---|
| `name` | string | Unique name, e.g. `inference-v1` |
| `paths` | string[] | URL prefix, e.g. `["/v1"]` |
| `methods` | string[] | HTTP methods allowed |
| `strip_path` | bool | `false` for all inference routes (path forwarded as-is) |
| `service` (FK) | Service | Upstream service |

**Routes provisioned**:

| Route Name | Path | Methods | Service | Strip Path |
|---|---|---|---|---|
| `inference-v1` | `/v1` | GET, POST | `litellm-inference` | false |
| `inference-v2` | `/v2` | GET, POST | `litellm-inference` | false |
| `embeddings` | `/v1/embeddings` | POST | `litellm-embeddings` | false |
| `health` | `/health` | GET | `litellm-health` | true |
| `spend-report` | `/v1/spend` | GET | `portal-backend` | false |
| `key-management` | `/v1/key` | GET, POST, DELETE | `litellm-admin` | true |

**Priority note**: `/v1/embeddings` is registered on a separate service (`litellm-embeddings`) to inherit the 120 s timeout. Kong matches the most specific path first, so `/v1/embeddings` takes precedence over `/v1` for embedding requests.

---

### Plugin

A behaviour applied to a Consumer, Route, Service, or globally.

| Field | Type | Notes |
|---|---|---|
| `name` | string | Plugin identifier, e.g. `key-auth` |
| `config` | object | Plugin-specific configuration |
| `scope` | enum | `global`, `service`, `route` |

**Plugins provisioned**:

#### Global Plugins

| Plugin | Scope | Key Config |
|---|---|---|
| `key-auth` | global | `key_names: [Authorization]`, `hide_credentials: true`, `anonymous: <anon-uuid>` |
| `acl` | global | `deny: [anonymous]` — blocks the anonymous consumer on all routes by default |
| `correlation-id` | global | `header_name: X-Request-ID`, `generator: uuid`, `echo_downstream: true` |
| `response-transformer` | global | `add.headers: [X-Platform: inference-platform, X-API-Version: 1]` |

#### Route-level Plugins

| Plugin | Route | Config | Purpose |
|---|---|---|---|
| `acl` | `health` | `allow: [anonymous, api-consumers]` | Permit unauthenticated health probes |

#### Consumer ACL Groups

| Consumer | Group |
|---|---|
| `smoke-test-consumer` | `api-consumers` |
| `anonymous` | `anonymous` |

---

## State Transitions

### Kong startup sequence

```
postgres (healthy)
  → kong-migration (bootstrap + up → exits 0)
    → kong (starts, connects to postgres, loads routes from DB)
      → make seed-kong (Admin API: create consumers, credentials, services, routes, plugins)
```

### Request auth flow

```
Incoming request
  → kong: key-auth global plugin
      ├─ Authorization header present + valid key → consumer identified → ACL check
      │     ├─ consumer in api-consumers group → route permitted → upstream
      │     └─ consumer in anonymous group + route has ACL deny[anonymous] → 403
      └─ Authorization header absent/invalid + anonymous configured → consumer = anonymous
            ├─ route = /health → ACL allow[anonymous] → upstream (200)
            └─ route = /v1/* → ACL deny[anonymous] → 403
```

**Note on 401 vs 403**: When `anonymous` is configured on the key-auth plugin, truly invalid keys return 401 (key-auth rejects before anonymous fallback). Missing keys fall through to anonymous consumer → ACL deny returns 403. The spec requires "HTTP 401" for rejection. This is addressed in `quickstart.md` with the implementation note on removal of `anonymous` from routes that must return 401.

---

## Database

All entities above are stored in the `kong` PostgreSQL database (host: `postgres`, port: 5432). Schema is managed exclusively by `kong migrations`. The platform's `scripts/init-db.sql` creates the `kong` database; Kong migration creates all tables.
