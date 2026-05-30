# Research: API Gateway Authentication

**Feature**: 012-kong-api-gateway-auth
**Date**: 2026-05-30

---

## Decision 1: Kong Deployment Mode — DB-less vs DB-backed

**Decision**: Switch from DB-less (declarative `kong.yml`) to PostgreSQL-backed mode.

**Rationale**: DB-backed mode allows runtime configuration via the Admin API without requiring Kong restarts. `seed-kong.sh` can create and verify all entities idempotently. Enables future runtime changes (new consumers, rate-limit config updates) without image rebuilds. The `kong` PostgreSQL database already exists in `scripts/init-db.sql`.

**Alternatives considered**:
- **Keep DB-less**: Simpler, no migration container needed. Rejected because it makes runtime consumer provisioning impossible and requires Kong restart for any config change.
- **Hybrid (DB-less + Admin API cache)**: Not supported in Kong 3.6 — a Kong instance is either DB-less or DB-backed, not both.

**Impact**: `kong.yml` is no longer loaded. `KONG_DECLARATIVE_CONFIG` env var is removed. `KONG_DATABASE=postgres` + `KONG_PG_*` vars are added. One-shot `kong-migration` container is added.

---

## Decision 2: Health Route Auth Exemption with Global key-auth

**Decision**: Global `key-auth` plugin with `config.anonymous = <anonymous-consumer-uuid>` + per-route `acl` plugin on inference routes blocking the anonymous consumer.

**Rationale**: Kong's standard pattern for "global auth except specific routes" is the anonymous consumer pattern. When `anonymous` is configured on the key-auth plugin, unauthenticated requests are tagged as the anonymous consumer and allowed to proceed. Adding an `acl` plugin on inference services blocks the anonymous consumer, effectively enforcing auth there. The health route has no `acl` plugin so anonymous passes through freely.

**Alternatives considered**:
- **Service-scoped key-auth (no global)**: Apply key-auth only to the litellm and portal services, not to the health service. Simpler — no anonymous consumer needed. Rejected because the user explicitly specified global key-auth scope.
- **Route-level plugin override to disable global**: Not cleanly supported in Kong 3.6 — route-level plugins augment, not disable, global plugins for the same plugin type.
- **Separate health service behind different Kong listener**: Over-engineering. Health is a simple unauthenticated probe.

**Implementation**:
1. Create consumer `anonymous` (no credentials).
2. Global `key-auth` plugin: `key_names: [Authorization]`, `anonymous: <anon-uuid>`, `hide_credentials: true`.
3. Global `acl` plugin: `deny: [anonymous]` — blocks the anonymous consumer on all routes.
4. Health route: add route-level `acl` plugin with `allow: [anonymous, api-consumers]` to explicitly permit the anonymous consumer through.

Wait — this creates a conflict: global ACL denies anonymous, health route ACL overrides to allow it. Kong's plugin precedence (route > service > global) means the route-level ACL wins for the health route. ✓

---

## Decision 3: API Key Format — Raw vs Bearer prefix

**Decision**: Store and send the raw key in the `Authorization` header without a `Bearer ` prefix.

**Rationale**: Kong's `key-auth` plugin reads the full value of the header named in `key_names`. If `Authorization` is the header and the client sends `Authorization: Bearer sk-xxx`, Kong looks for a credential matching the string `Bearer sk-xxx`, not `sk-xxx`. Storing raw keys avoids this mismatch and keeps key-auth simple.

**Alternatives considered**:
- **Bearer prefix**: Client sends `Authorization: Bearer <key>`, key stored as `Bearer <key>` in Kong. Works but is non-standard (Bearer is an OAuth2 token type, not a raw API key marker). Rejected for clarity.
- **Kong JWT plugin**: For Bearer tokens / OAuth2 flows. Out of scope for this feature (Keycloak SSO is a separate auth layer).
- **Custom header `X-API-Key`**: Also valid, but `Authorization` is the existing convention in this platform and changing it would break current clients.

**Impact**: `smoke-test.sh` and any documentation must send `Authorization: <raw-key>` not `Authorization: Bearer <key>`. The existing `seed-kong.sh` example showing `Bearer %s` is corrected in the rewrite.

---

## Decision 4: kong-migration Container Strategy

**Decision**: One-shot `kong-migration` service with `command: kong migrations bootstrap && kong migrations up`, `restart: on-failure`, and Kong service `depends_on: kong-migration: condition: service_completed_successfully`.

**Rationale**: `kong migrations bootstrap` is idempotent on re-runs (errors if already bootstrapped are suppressed with `|| true`). `kong migrations up` applies any pending migrations. The `service_completed_successfully` condition ensures Kong starts only after migration succeeds. This is the documented Kong Docker pattern.

**Alternatives considered**:
- **Init container in Kubernetes style**: Not natively supported in Docker Compose before version 3.9 `depends_on` condition support. The `service_completed_successfully` condition is available in Compose spec 3.9 and Docker Compose v2.x — both available in this project.
- **Entrypoint wrapper on Kong container**: Adds complexity to the Kong image startup. Rejected in favour of a clean separation of concerns.

**Impact**: `docker-compose.yml` gains a `kong-migration` service in the `core` profile. Migration runs once; subsequent `docker compose up` calls skip it because the container exits 0.

---

## Decision 5: /v2 Route

**Decision**: Create a `/v2` route in Kong pointing to the same LiteLLM upstream as `/v1`, with no additional routing logic.

**Rationale**: Constitution §4.2 mandates that breaking changes go to `/v2` with a 6-month deprecation notice. Creating the Kong route now (even with no traffic) ensures the path exists and is authenticated. Actual `/v2` endpoint behaviour is defined in a future feature.

**Alternatives considered**:
- **Defer until /v2 is needed**: Simpler now, but risks the path being missed when a breaking change is introduced. Rejected.
- **Return 501 from /v2**: Would require a `request-termination` plugin. Rejected — empty route is cleaner; LiteLLM returns 404 for unrecognised paths which is acceptable.

---

## Decision 6: Timeout Configuration per Route

**Decision**: Carry forward existing per-service timeouts from `kong.yml`:
- Inference (`/v1`, `/v2`): `read_timeout: 60 000 ms`
- Embeddings (`/v1/embeddings`): `read_timeout: 120 000 ms` (batch embedding batches)
- Health: `read_timeout: 10 000 ms`
- Admin (`/v1/key`): `read_timeout: 30 000 ms`
- Spend (`/v1/spend`): `read_timeout: 15 000 ms`

**Rationale**: These values were established in earlier features (011-embeddings-endpoint set the 120 s timeout). Moving to DB mode does not change traffic characteristics.

---

## All NEEDS CLARIFICATION markers: None

No clarification markers were present in the spec. All decisions above are resolved.
