# Tasks: Enterprise SSO JWT Validation

**Input**: Design documents from `specs/024-enterprise-sso-jwt/`

**Prerequisites**: plan.md ✅ spec.md ✅ research.md ✅ data-model.md ✅ contracts/ ✅ quickstart.md ✅

**Tests**: Not explicitly requested — no test tasks generated. Acceptance is verified via `make smoke` (curl-based, per Constitution §V).

**Organization**: Tasks grouped by user story to enable independent validation of each story.

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Parallelisable — touches different files, no dependency on incomplete sibling tasks
- **[Story]**: Maps to user story in spec.md (US1–US5)
- Exact file paths included in every task description

---

## Phase 1: Setup — Environment & Makefile

**Purpose**: Wire env vars and Makefile targets before any service is started. No service dependencies.

- [x] T001 Extend `.env.example` with Keycloak and next-auth variables: `KEYCLOAK_ADMIN`, `KEYCLOAK_ADMIN_PASSWORD`, `KEYCLOAK_REALM`, `INFERENCE_GATEWAY_CLIENT_SECRET`, `PLATFORM_UI_CLIENT_SECRET`, `PHOENIX_UI_CLIENT_SECRET`, `LANGFUSE_UI_CLIENT_SECRET`, `NEXTAUTH_SECRET`, `NEXTAUTH_URL`
- [x] T002 Add `seed-kong-jwt` target to `Makefile`: `bash scripts/seed-kong-jwt.sh` (alongside existing `seed-kong` and `seed-vault` targets)
- [x] T003 [P] Create directory `services/keycloak/` (will hold `realm-export.json`)

**Checkpoint**: `.env.example` documents all new variables; `make seed-kong-jwt` target exists.

---

## Phase 2: Foundational — Keycloak Service + Realm

**Purpose**: Keycloak must be running and the realm imported before Kong jwt credentials can be seeded. Blocks all user story work.

**⚠️ CRITICAL**: US1–US5 cannot be tested until Keycloak is healthy and `make seed-kong-jwt` has run.

- [x] T004 Add `keycloak` service to `docker-compose.yml` under profiles `[auth, all]`: image `quay.io/keycloak/keycloak:24.0.3`, command `start-dev --import-realm`, port `8083:8083`, mount `./services/keycloak:/opt/keycloak/data/import`, env vars `KEYCLOAK_ADMIN`, `KEYCLOAK_ADMIN_PASSWORD`, `KC_DB=postgres`, `KC_DB_URL=jdbc:postgresql://postgres:5432/keycloak`, `KC_DB_USERNAME`, `KC_DB_PASSWORD`, `KC_HOSTNAME_STRICT=false`, `KC_HTTP_PORT=8083`; add `depends_on: postgres: condition: service_healthy`; add healthcheck `curl -f http://localhost:8083/realms/inference-platform/.well-known/openid-configuration`; add `keycloak_data` volume
- [x] T005 Create `services/keycloak/realm-export.json` with: realm name `inference-platform`; access token lifespan 3600 s; realm roles `admin`, `engineer`, `viewer`; four OIDC clients — `inference-gateway` (confidential, serviceAccountsEnabled, client credentials + PKCE, audience mapper → `inference-gateway`), `platform-ui` (public, PKCE only, audience mapper → `platform-ui`), `phoenix-ui` (bearer-only, audience mapper → `phoenix-ui`), `langfuse-ui` (bearer-only, audience mapper → `langfuse-ui`); protocol mappers on `inference-gateway` and `platform-ui`: realm-roles mapper → claim `roles`, user-attribute mapper (`team`) → claim `team`
- [x] T006 [P] Create `scripts/seed-kong-jwt.sh`: wait for Keycloak healthcheck; fetch realm RS256 public key from `GET http://keycloak:8083/realms/inference-platform` → `$.public_key` field; wrap in PEM headers; POST or PATCH Kong consumer `keycloak-realm`; POST or PATCH JWT credential with `key=http://keycloak:8083/realms/inference-platform`, `algorithm=RS256`, `rsa_public_key=<PEM>`; repeat for consumers `phoenix-realm` (key=`phoenix-ui`) and `langfuse-realm` (key=`langfuse-ui`) using same PEM; make script idempotent (PUT/PATCH if credential exists)

**Checkpoint**: `make up-auth` starts Keycloak; `make seed-kong-jwt` prints success; `curl -sf http://localhost:8001/consumers/keycloak-realm/jwt` returns JWT credential with RS256 key.

---

## Phase 3: User Story 1 — Developer Authenticates and Calls the API (Priority: P1) 🎯 MVP

**Goal**: Replace `key-auth` with `jwt` plugin on all `/v1/*` routes. A valid Keycloak JWT → `200 OK`; no token → `401`.

**Independent Test**:
```bash
TOKEN=$(curl -s -X POST http://localhost:8083/realms/inference-platform/protocol/openid-connect/token \
  -d grant_type=client_credentials -d client_id=inference-gateway \
  -d client_secret=$INFERENCE_GATEWAY_CLIENT_SECRET | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/v1/models -H "Authorization: Bearer $TOKEN"
# Expected: 200
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/v1/models
# Expected: 401
```

- [x] T007 [US1] Update `services/kong/kong.yml`: remove `key-auth` plugin from routes `litellm-proxy`, `models-catalogue`, `litellm-embeddings-route`, `cache-flush`, `spend-report`; add global `jwt` plugin config with `key_claim_name: iss`, `claims_to_verify: [exp, nbf]`, `clock_skew: 30`, `anonymous: ~`, `header_names: [Authorization]`, `run_on_preflight: true`; add consumers section with `keycloak-realm` consumer; retain `smoke-test-consumer` entry but remove its `keyauth_credentials` section
- [x] T008 [US1] Add `jwt_secrets` section to `services/kong/kong.yml` declarative config: entry for `keycloak-realm` consumer with `key: http://keycloak:8083/realms/inference-platform`, `algorithm: RS256`, `rsa_public_key: ""` placeholder (populated by `seed-kong-jwt.sh` via Kong Admin API at runtime, not in declarative file — document this in a comment)
- [x] T009 [US1] Update `scripts/smoke-test.sh`: add a `get_token()` function that obtains a client-credentials JWT from Keycloak using `$INFERENCE_GATEWAY_CLIENT_SECRET`; replace all `Authorization: $SMOKE_API_KEY` references with `Authorization: Bearer $(get_token)`; add a fallback comment for when Keycloak is not running (auth profile not active)

**Checkpoint**: `make smoke` passes all `/v1/*` checks with JWT auth. `curl` without token returns `401`.

---

## Phase 4: User Story 2 — Gateway Rejects Expired and Tampered Tokens (Priority: P1)

**Goal**: Cryptographic rejection at Kong edge — expired, signature-invalid, or wrong-issuer tokens never reach Guardrails.

**Independent Test**:
```bash
# Expired token (modify exp to past timestamp)
EXPIRED_TOKEN=$(python3 -c "
import base64, json, time
parts = '$TOKEN'.split('.')
payload = json.loads(base64.urlsafe_b64decode(parts[1] + '=='))
payload['exp'] = int(time.time()) - 3600
parts[1] = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b'=').decode()
print('.'.join(parts))")
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/v1/models -H "Authorization: Bearer $EXPIRED_TOKEN"
# Expected: 401

# Tampered payload
TAMPERED=$(echo "$TOKEN" | python3 -c "
import sys; t = sys.stdin.read().strip().split('.')
t[1] = 'AAAA' + t[1][4:]
print('.'.join(t))")
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/v1/models -H "Authorization: Bearer $TAMPERED"
# Expected: 401
```

- [x] T010 [US2] Add structured error response formatting for JWT rejections in `services/kong/kong.yml`: add route-level `post-function` plugin (phase: `header_filter`) on all `/v1/*` routes that rewrites Kong's default `{"message": "Unauthorized"}` error body to the platform schema `{"error": "Unauthorized", "message": "<reason>", "detail": {"reason": "<reason_code>"}}` when status is 401 or 403
- [x] T011 [US2] Add expired-token and tampered-token test cases to `scripts/smoke-test.sh`: craft expired JWT (modify `exp` to past timestamp, strip signature), craft tampered JWT (flip bytes in payload), assert both return `401`; add missing-`roles` claim test asserting `403`; add missing-`team` claim test asserting `403`

**Checkpoint**: Expired token → `401`; tampered token → `401`; missing claims → `403`. All verified by `make smoke`.

---

## Phase 5: User Story 3 — Role and Team Claims Drive Downstream Authorisation (Priority: P2)

**Goal**: Kong extracts `roles` and `team` from validated JWT and forwards as `X-User-Roles`, `X-User-Team`, `X-User-Sub` headers. OPA and Guardrails consume them.

**Independent Test**:
```bash
# Inspect guardrails logs for forwarded headers after a valid request
TOKEN=$(get_token)
curl -sf http://localhost:8080/v1/models -H "Authorization: Bearer $TOKEN" > /dev/null
make logs svc=guardrails 2>&1 | grep "X-User-Roles\|x-user-roles" | tail -3
# Expected: log entry containing roles array and team value
```

- [x] T012 [US3] Create `services/kong/plugins/extract-jwt-claims.lua`: Lua script that reads `Authorization: Bearer <token>` header, base64url-decodes the payload (middle segment), parses JSON, sets `X-User-Roles` (JSON array string), `X-User-Team` (string), `X-User-Sub` (UUID string) as upstream request headers; handles missing claims gracefully (skip header if claim absent); runs only when Authorization header is present
- [x] T013 [US3] Update `services/kong/kong.yml`: add `post-function` plugin to routes `litellm-proxy` and `litellm-embeddings-route` with `config.access` containing the Lua from `extract-jwt-claims.lua` (inline in declarative config); add `request-transformer` plugin to all `/v1/*` routes to strip any client-supplied `X-User-Roles`, `X-User-Team`, `X-User-Sub` headers before the post-function sets them (defence in depth — prevents header spoofing)
- [x] T014 [P] [US3] Update `services/opa/policies/inference.rego` (create file + directory if not existing at `services/opa/policies/`): add rules that read `input.request.http.headers["x-user-roles"]` (parse JSON array) and `input.request.http.headers["x-user-team"]`; define `allow` rule: `viewer` role blocked from non-GET operations; `engineer` and `admin` roles allowed all model access; `admin` role allowed `/cache/flush` and `/v1/spend`
- [x] T015 [P] [US3] Update `services/guardrails/main.py`: in the request handler, read `X-User-Roles`, `X-User-Team`, `X-User-Sub` from incoming request headers; include `roles`, `team`, `sub` fields in the structured audit log entry emitted to Loki alongside existing `request_id`, `event_type`, `model`, `pii_entity_count`, `scanner_blocked` fields

**Checkpoint**: OPA decision log shows `roles` and `team` from JWT. Guardrails audit log entry contains `roles`, `team`, `sub` fields. Verified via `make logs svc=guardrails` and `make logs svc=opa`.

---

## Phase 6: User Story 5 — Platform Admin Accesses Phoenix and Langfuse UIs (Priority: P2)

**Goal**: Phoenix UI (/phoenix) and Langfuse UI (/langfuse) accessible through Kong with JWT validation; audience-isolated per service.

**Independent Test**:
```bash
# No token → 401
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/phoenix
# Expected: 401

# API token (wrong audience) → 401
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/phoenix -H "Authorization: Bearer $TOKEN"
# Expected: 401 (aud mismatch)

# Phoenix-scoped token → proxied response
PHOENIX_TOKEN=$(curl -s -X POST http://localhost:8083/.../token -d client_id=phoenix-ui ...)
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/phoenix -H "Authorization: Bearer $PHOENIX_TOKEN"
# Expected: 200 (Phoenix UI HTML)
```

- [x] T016 [US5] Update `services/kong/kong.yml`: add service `phoenix-ui` → `http://arize-phoenix:6006` with `connect_timeout: 5000`, `read_timeout: 30000`; add route `phoenix-ui-route` on path `/phoenix`, `strip_path: true`; attach `jwt` plugin with `key_claim_name: aud`, `claims_to_verify: [exp, nbf]`, `clock_skew: 30`; add service `langfuse-ui` → `http://langfuse-server:3002`; add route `langfuse-ui-route` on path `/langfuse`, `strip_path: true`; attach `jwt` plugin with `key_claim_name: aud`
- [x] T017 [US5] Update `scripts/seed-kong-jwt.sh`: add creation of consumer `phoenix-realm` with JWT credential `key=phoenix-ui`, `algorithm=RS256`, same `rsa_public_key` PEM; add creation of consumer `langfuse-realm` with JWT credential `key=langfuse-ui`, same PEM; ensure all three consumer creates are idempotent
- [x] T018 [P] [US5] Add Phoenix and Langfuse UI smoke test cases to `scripts/smoke-test.sh`: assert unauthenticated `GET /phoenix` → `401`; assert API-scoped token on `/phoenix` → `401` (wrong aud); assert unauthenticated `GET /langfuse` → `401`

**Checkpoint**: `curl http://localhost:8080/phoenix` without token → `401`. Phoenix-ui-scoped token → `200` with Phoenix HTML. Langfuse UI same pattern.

---

## Phase 7: User Story 4 — Token Refresh Without User Disruption (Priority: P3)

**Goal**: New tokens obtained after expiry are immediately accepted. Key rotation recovery ≤5 minutes via `make seed-kong-jwt`.

**Independent Test**:
```bash
# Simulate key rotation: re-run seed, obtain new token, verify accepted
make seed-kong-jwt
NEW_TOKEN=$(get_token)
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/v1/models -H "Authorization: Bearer $NEW_TOKEN"
# Expected: 200 (no Kong restart needed)
```

- [x] T019 [US4] Verify `scripts/seed-kong-jwt.sh` is fully idempotent: use `PUT /consumers/{username}` (not POST) and `PUT /consumers/{username}/jwt/{id}` so re-running after key rotation replaces the existing credential rather than creating duplicates; add a pre-check that reads existing credential ID before updating
- [x] T020 [US4] Add key-rotation smoke test to `scripts/smoke-test.sh`: call `seed-kong-jwt.sh` inline; obtain fresh token; assert `200`; document as the SC-004 verification step

**Checkpoint**: `make seed-kong-jwt` runs twice without error. Fresh token after re-seed is accepted. No Kong restart needed.

---

## Phase 8: Platform UI — next-auth v5 Keycloak Session (supports US1 browser flow)

**Goal**: platform-ui session management via Keycloak OIDC; next-auth v5 configured with `roles` and `team` in session object.

- [x] T021 Create `services/platform-ui/` directory with minimal Next.js 15 shell if not existing; install `next-auth` v5 and `@auth/core` in `services/platform-ui/package.json`
- [x] T022 [P] Create `services/platform-ui/auth.ts`: configure next-auth v5 with `Keycloak` provider (issuer: `http://keycloak:8083/realms/inference-platform`, clientId: `platform-ui`, clientSecret: `process.env.PLATFORM_UI_CLIENT_SECRET`); add `jwt` callback to persist `access_token`, `roles` (from `token.roles`), and `team` (from `token.team`) into the next-auth JWT; add `session` callback to expose `roles`, `team`, and `access_token` in `session.user`
- [x] T023 [P] Create `services/platform-ui/middleware.ts`: use `auth` from `./auth` to protect all routes; allow unauthenticated access to `/api/auth/*` and `/health`; redirect unauthenticated requests to Keycloak login
- [x] T024 Create `services/platform-ui/app/api/auth/[...nextauth]/route.ts`: export `{ GET, POST }` handlers from next-auth `auth.ts`

**Checkpoint**: `GET http://localhost:3001` (when platform-ui is running) redirects unauthenticated users to Keycloak login. After login, `session.user.roles` and `session.user.team` are populated.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [x] T025 [P] Update `docs/progress.md`: mark feature 024 `enterprise-sso-jwt` as active; list completed features 001–023
- [x] T026 Add `keycloak_data` volume entry to `docker-compose.yml` `volumes:` block (alongside `pg_data`, `phoenix_data` etc.)
- [x] T027 [P] Add Keycloak health endpoint to `scripts/smoke-test.sh` pre-flight check: assert `GET http://localhost:8083/realms/inference-platform/.well-known/openid-configuration` returns `200` when auth profile is active
- [x] T028 Run `make smoke` end-to-end and confirm all SC-001–SC-009 acceptance criteria pass; fix any regressions in existing smoke checks caused by replacing `key-auth` with `jwt`

**Checkpoint**: `make smoke` green. `make stats` memory within `auth` profile budget (~2.81 GB).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — Keycloak env vars and Makefile target must exist before `make up-auth`
- **Phase 3 (US1)**: Depends on Phase 2 — Keycloak must be running and seeded
- **Phase 4 (US2)**: Depends on Phase 3 — same `jwt` plugin config, adds error formatting and smoke test cases
- **Phase 5 (US3)**: Depends on Phase 3 — post-function runs after `jwt` plugin validates
- **Phase 6 (US5)**: Depends on Phase 2 — Keycloak clients (phoenix-ui, langfuse-ui) must exist in realm
- **Phase 7 (US4)**: Depends on Phase 3 — validates idempotency of seed script established in Phase 2/3
- **Phase 8 (Platform UI)**: Depends on Phase 2 — Keycloak OIDC well-known endpoint must be reachable
- **Phase 9 (Polish)**: Depends on Phases 3–8

### User Story Dependencies

- **US1 (P1)**: Unblocked after Phase 2
- **US2 (P1)**: Unblocked after Phase 3 (jwt plugin must be active)
- **US3 (P2)**: Unblocked after Phase 3 (claims extracted from validated tokens)
- **US5 (P2)**: Unblocked after Phase 2 (separate Keycloak clients must exist)
- **US4 (P3)**: Unblocked after Phase 3 (validates seed script idempotency)
- **Platform UI**: Unblocked after Phase 2 (independent of US1–US5 Kong changes)

### Within Each Phase

- Parallelisable tasks within a phase (marked `[P]`) touch different files and can run concurrently
- `T004` (docker-compose Keycloak service) must complete before `make up-auth` can be run
- `T005` (realm-export.json) must complete before Keycloak starts (it's mounted at boot)
- `T007` (kong.yml jwt plugin) must complete before any JWT smoke test can pass
- `T012` and `T013` (Lua post-function) must complete before US3 headers are forwarded

---

## Parallel Execution Examples

### Phase 2 — Foundational (T004, T005, T006 can start in parallel after T001–T003)

```
T004: docker-compose.yml keycloak service    [kong.yml untouched]
T005: services/keycloak/realm-export.json    [docker-compose untouched]
T006: scripts/seed-kong-jwt.sh               [realm-export untouched]
```

### Phase 5 — US3 (T014 and T015 can run in parallel after T012+T013)

```
T014: services/opa/policies/inference.rego   [guardrails untouched]
T015: services/guardrails/main.py            [opa untouched]
```

### Phase 8 — Platform UI (T022, T023 can run in parallel after T021)

```
T022: services/platform-ui/auth.ts           [middleware.ts untouched]
T023: services/platform-ui/middleware.ts     [auth.ts untouched]
```

---

## Implementation Strategy

### MVP First (US1 only — Phases 1–3)

1. Phase 1: Update `.env.example`, `Makefile`
2. Phase 2: Add Keycloak to docker-compose, create realm-export.json, write seed script
3. Phase 3: Update `kong.yml` (jwt plugin), update `smoke-test.sh` (JWT token acquisition)
4. **STOP and VALIDATE**: `make up-auth && make seed-kong-jwt && make smoke` — valid token → 200, no token → 401
5. Demo and confirm before proceeding to US2

### Incremental Delivery

1. Phase 1–3 → US1 working (API auth gate) — MVP
2. Phase 4 → US2 working (expired/tampered rejection confirmed in smoke tests)
3. Phase 5 → US3 working (roles/team headers forwarded; OPA consuming them)
4. Phase 6 → US5 working (Phoenix and Langfuse UIs protected)
5. Phase 7 → US4 verified (key rotation idempotency confirmed)
6. Phase 8 → Platform UI session management
7. Phase 9 → Polish, memory check, full smoke test

---

## Notes

- `[P]` tasks touch distinct files — safe to run concurrently in the same session
- `[US#]` label maps each task to its user story for traceability and MVP scoping
- `services/kong/kong.yml` is modified by tasks T007, T008, T010, T013, T016 — these must be sequential
- `scripts/smoke-test.sh` is modified by T009, T011, T018, T020, T027 — sequential edits
- `scripts/seed-kong-jwt.sh` is modified by T006 (created) and T017, T019 (extended) — sequential
- No custom Python service created — this feature is pure configuration (YAML, JSON, Lua, Bash, TypeScript)
- Total tasks: **28** (T001–T028)
