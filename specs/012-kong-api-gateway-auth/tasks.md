# Tasks: API Gateway Authentication

**Input**: Design documents from `specs/012-kong-api-gateway-auth/`

**Prerequisites**: plan.md ✓ spec.md ✓ research.md ✓ data-model.md ✓ contracts/gateway-api.md ✓ quickstart.md ✓

**Tests**: No test tasks generated (not requested in spec). Validation is via `curl` assertions in `smoke-test.sh` (see Polish phase).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US4)
- Exact file paths are included in every task description

---

## Phase 1: Setup (Environment Variables)

**Purpose**: Ensure environment variables required for DB-backed Kong are defined before any container changes.

- [x] T001 [P] Verify `.env.example` contains `KONG_PG_PASSWORD`, `KONG_PG_USER`, `KONG_PG_DATABASE` entries (names only, no values); add any that are missing
- [x] T002 [P] Verify `.env` contains working local values for `KONG_PG_PASSWORD`, `KONG_PG_USER`, `KONG_PG_DATABASE` (e.g. `kong`/`kong`/`kong` for local dev); add any that are missing

> T001 and T002 touch different files and can run in parallel.

---

## Phase 2: Foundational (DB-backed Kong Infrastructure)

**Purpose**: Switch Kong from DB-less declarative mode to PostgreSQL-backed mode. This blocks all user story work — Kong must be running in DB mode before seeding can begin.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [x] T003 Add `kong-migration` one-shot service to `docker-compose.yml` under the `core` profile: image `kong:3.6`, environment vars `KONG_DATABASE=postgres` + `KONG_PG_HOST=postgres` + `KONG_PG_PORT=5432` + `KONG_PG_DATABASE=${KONG_PG_DATABASE:-kong}` + `KONG_PG_USER=${KONG_PG_USER:-kong}` + `KONG_PG_PASSWORD=${KONG_PG_PASSWORD}`, command `sh -c "kong migrations bootstrap 2>/dev/null || true && kong migrations up"`, `depends_on: postgres: condition: service_healthy`, `restart: on-failure`
- [x] T004 Update the `kong` service in `docker-compose.yml`: remove `KONG_DATABASE: "off"` and `KONG_DECLARATIVE_CONFIG` env vars and the `./services/kong/kong.yml` volume mount; add `KONG_DATABASE: postgres` + `KONG_PG_HOST: postgres` + `KONG_PG_PORT: "5432"` + `KONG_PG_DATABASE: ${KONG_PG_DATABASE:-kong}` + `KONG_PG_USER: ${KONG_PG_USER:-kong}` + `KONG_PG_PASSWORD: ${KONG_PG_PASSWORD}`; add `kong-migration: condition: service_completed_successfully` to the existing `depends_on` block

**Checkpoint**: `docker compose --profile core up -d` starts postgres → kong-migration (exits 0) → kong (DB mode, empty config). `curl -sf http://localhost:8001/status` returns 200.

---

## Phase 3: User Stories 1 & 2 — Authenticated Access and Rejection (Priority: P1) 🎯 MVP

**Goal**: Every inference endpoint requires a valid API key in the `Authorization` header. Requests without a valid key receive HTTP 401 before reaching LiteLLM.

**Why combined**: US1 (auth success) and US2 (auth rejection) are two sides of the same key-auth enforcement. They share all the same seeding infrastructure and are independently testable only when both exist.

**Independent Test**:
```bash
make seed-kong
# Expect 200:
curl -sf http://localhost:8080/v1/models -H "Authorization: ${SMOKE_API_KEY}"
# Expect 401:
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/v1/models
```

### Implementation for User Stories 1 & 2

- [x] T005 [US1] [US2] Rewrite `scripts/seed-kong.sh`: replace the entire file with the new DB-mode script scaffold — shebang `#!/usr/bin/env bash`, `set -euo pipefail`, `KONG_ADMIN="${KONG_ADMIN_URL:-http://localhost:8001}"`, helper functions `ok()` / `info()` / `err()` / `fail()`, `wait_for_kong()` that polls `GET ${KONG_ADMIN}/status` up to 30 attempts with 2 s sleep, and a `main()` that calls (in order): `wait_for_kong`, `create_consumers`, `create_inference_service`, `create_embeddings_service`, `create_admin_services`, `create_health_service`, `create_global_plugins`, `verify_setup`, `print_key`
- [x] T006 [US1] [US2] Add `create_consumers()` to `scripts/seed-kong.sh`: idempotent `PUT ${KONG_ADMIN}/consumers/smoke-test-consumer` (body: `username=smoke-test-consumer&tags[]=smoke-test`); then `POST ${KONG_ADMIN}/consumers/smoke-test-consumer/key-auth` with `key=${SMOKE_API_KEY}` (suppress duplicate-key error with `|| true`); fail with `err` if `SMOKE_API_KEY` is unset
- [x] T007 [US1] [US2] Add `create_inference_service()` to `scripts/seed-kong.sh`: `PUT ${KONG_ADMIN}/services/litellm-inference` (url=`http://litellm:4000`, connect_timeout=10000, read_timeout=60000, write_timeout=60000); `PUT ${KONG_ADMIN}/services/litellm-inference/routes/inference-v1` (paths[]=/v1, methods[]=GET, methods[]=POST, strip_path=false); `PUT ${KONG_ADMIN}/services/litellm-inference/routes/inference-v2` (paths[]=/v2, methods[]=GET, methods[]=POST, strip_path=false); `POST ${KONG_ADMIN}/services/litellm-inference/plugins` for `key-auth` (config.key_names[]=Authorization, config.hide_credentials=true, config.key_in_header=true)
- [x] T008 [US1] [US2] Add `create_embeddings_service()` to `scripts/seed-kong.sh`: `PUT ${KONG_ADMIN}/services/litellm-embeddings` (url=`http://litellm:4000`, connect_timeout=10000, read_timeout=120000, write_timeout=120000); `PUT ${KONG_ADMIN}/services/litellm-embeddings/routes/embeddings` (paths[]=/v1/embeddings, methods[]=POST, strip_path=false); `POST ${KONG_ADMIN}/services/litellm-embeddings/plugins` for `key-auth` (same config as inference service)
- [x] T009 [US1] [US2] Add `create_admin_services()` to `scripts/seed-kong.sh`: `PUT ${KONG_ADMIN}/services/portal-backend` (url=`http://portal-backend:8092`, read_timeout=15000) + `PUT` route `spend-report` (paths[]=/v1/spend, methods[]=GET, strip_path=false) + service-level `key-auth` plugin; `PUT ${KONG_ADMIN}/services/litellm-admin` (url=`http://litellm:4000`, read_timeout=30000) + `PUT` route `key-management` (paths[]=/v1/key, methods[]=GET+POST+DELETE, strip_path=true) + service-level `key-auth` plugin
- [x] T010 [US1] [US2] Add `create_global_plugins()` to `scripts/seed-kong.sh`: `POST ${KONG_ADMIN}/plugins` for `correlation-id` (config.header_name=X-Request-ID, config.generator=uuid, config.echo_downstream=true); `POST ${KONG_ADMIN}/plugins` for `response-transformer` (config.add.headers[]=X-Platform:inference-platform, config.add.headers[]=X-API-Version:1); guard each POST with a check against `GET ${KONG_ADMIN}/plugins?name=<plugin>` to skip if already installed
- [x] T011 [US1] [US2] Add `verify_setup()` to `scripts/seed-kong.sh`: confirm `GET ${KONG_ADMIN}/consumers/smoke-test-consumer` returns 200, print `ok` for consumer, credentials, and each service; add `print_key()` that prints the value of `${SMOKE_API_KEY}` with example `curl` commands showing both an authenticated request to `/v1/models` and an unauthenticated rejection

**Checkpoint**: `make seed-kong` completes without errors. `curl -sf http://localhost:8080/v1/models -H "Authorization: ${SMOKE_API_KEY}"` returns 200. `curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/v1/models` returns 401.

---

## Phase 4: User Story 3 — Health Without Authentication (Priority: P2)

**Goal**: `GET /health` returns 200 without any `Authorization` header. Infrastructure tooling and CI can probe liveness without credentials.

**Independent Test**:
```bash
curl -sf http://localhost:8080/health
# Must return 200 with no Authorization header
```

**Why this is its own phase**: The health service must have NO key-auth plugin. It requires a dedicated service entry so it is never accidentally covered by the inference service's service-level key-auth plugin.

### Implementation for User Story 3

- [x] T012 [US3] Add `create_health_service()` to `scripts/seed-kong.sh`: `PUT ${KONG_ADMIN}/services/litellm-health` (url=`http://litellm:4000`, connect_timeout=5000, read_timeout=10000, write_timeout=10000); `PUT ${KONG_ADMIN}/services/litellm-health/routes/health` (paths[]=/health, methods[]=GET, strip_path=true); do NOT add any key-auth plugin to this service or route

**Checkpoint**: `curl -sf http://localhost:8080/health` returns 200 with no `Authorization` header. Inference endpoints still require auth (T011 checkpoint still holds).

---

## Phase 5: User Story 4 — Internal Port Isolation (Priority: P2)

**Goal**: LiteLLM port 4000 (and all other internal service ports) are reachable only within the Docker network. No external client can connect to them directly.

**Independent Test**:
```bash
curl -sf --connect-timeout 2 http://localhost:4000/v1/models \
  && echo "FAIL" || echo "PASS: port not reachable"
```

### Implementation for User Story 4

- [x] T013 [US4] Verify `docker-compose.yml` `litellm` service uses `expose: ["4000"]` (internal-only) not `ports: ["4000:4000"]` (host-bound); confirm the inline comment `# Port 4000 has no host binding — all traffic via Kong :8080 (constitution §2.1)` is present; make no change if already correct
- [x] T014 [P] [US4] Audit `docker-compose.yml` for any other internal service (`redis`, `postgres`, `prometheus`, `loki`, `otel-collector`, `alertmanager`, `jaeger`, `phoenix`, `langfuse-server`, `langfuse-worker`, `keycloak`, `opa`, `vault`, `presidio-analyzer`, `presidio-anonymizer`, `llm-guard`, `guardrails`, `batch-worker`, `portal-backend`, `mlflow`) that has an unexpected host port binding (i.e. `ports:` instead of `expose:` for non-public services); fix any bindings found

**Checkpoint**: `docker inspect <container> | jq '.[].HostConfig.PortBindings'` for litellm shows `{}`. `curl --connect-timeout 2 http://localhost:4000/` fails with connection refused.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Extend `smoke-test.sh` with falsifiable acceptance criteria for all four user stories, satisfying constitution Principle V.

- [x] T015 Add auth-rejection assertions to `scripts/smoke-test.sh`: for each of `/v1/models` (GET), `/v1/chat/completions` (POST with `{}`), `/v1/embeddings` (POST with `{}`), assert that a request with no `Authorization` header returns HTTP 401; use `fail()` helper if status is not 401
- [x] T016 Add health-no-auth assertion to `scripts/smoke-test.sh`: `curl -sf http://localhost:8080/health` with no `Authorization` header; assert HTTP 200; fail with descriptive message if not 200
- [x] T017 Add internal-port assertion to `scripts/smoke-test.sh`: `curl -sf --connect-timeout 2 http://localhost:4000/v1/models`; assert the command FAILS (exit non-zero); if it succeeds, call `fail "LiteLLM port 4000 is externally reachable — constitution §2.1 violation"`

**Final Checkpoint**: `make smoke` passes all assertions including the new auth and port-isolation checks. `make stats` shows `core` profile within ~620 MB.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately; T001 and T002 are parallel (different files)
- **Phase 2 (Foundational)**: Depends on Phase 1 — T003 then T004 (same file, sequential). BLOCKS all user story phases.
- **Phase 3 (US1+US2)**: Depends on Phase 2 completion. T005 → T006 → T007/T008/T009 (same file, sequential) → T010 → T011.
- **Phase 4 (US3)**: Depends on Phase 2 completion. Can start in parallel with Phase 3 if T012 is implemented as a standalone function stub — but must be wired into main() only after T005 defines main().
- **Phase 5 (US4)**: Depends on Phase 2 only. T013 and T014 can run in parallel (T013 checks litellm, T014 audits all other services).
- **Phase 6 (Polish)**: Depends on Phases 3+4+5 all complete. T015, T016, T017 each touch `smoke-test.sh` in different sections but are sequential for safety.

### User Story Dependencies

- **US1+US2 (P1)**: Depends on Foundational (Phase 2). No dependency on US3 or US4.
- **US3 (P2)**: Depends on Foundational (Phase 2). No dependency on US1/US2 — health service is separate.
- **US4 (P2)**: Depends on Foundational (Phase 2) only — it is a verification of port configuration, not code.

### Within Phase 3

```
T005 (scaffold + main skeleton)
  → T006 (create_consumers)
    → T007 (create_inference_service)   ← write function body
    → T008 (create_embeddings_service)  ← write function body (same file, after T007)
    → T009 (create_admin_services)      ← write function body (same file, after T008)
      → T010 (create_global_plugins)
        → T011 (verify_setup + print_key)
```

All tasks T005–T011 write to `scripts/seed-kong.sh` and must be sequential.

---

## Parallel Opportunities

**Phase 1**: T001 (.env.example) ‖ T002 (.env)

**Phase 2**: T003+T004 must be sequential (same file), but Phase 1 and Phase 2 can overlap (T001/T002 while waiting for Phase 2 to start).

**Phase 5**: T013 (litellm port check) ‖ T014 (other services audit) — different sections of docker-compose.yml.

**No parallelism within Phase 3**: All seed-kong.sh tasks write to the same file.

---

## Implementation Strategy

### MVP First (User Stories 1 & 2 Only)

1. Complete Phase 1 (Setup): T001, T002
2. Complete Phase 2 (Foundational): T003, T004
3. Complete Phase 3 (US1+US2): T005–T011
4. **STOP and VALIDATE**: `make up-core && make seed-kong` then test 200 with key and 401 without
5. Proceed to US3 and US4 once MVP is validated

### Incremental Delivery

1. Phase 1+2 → Kong running in DB mode, empty config
2. Phase 3 → Auth-gated inference (US1+US2 — the core security guarantee)
3. Phase 4 → Health unauthenticated (US3 — operational necessity)
4. Phase 5 → Port isolation verified (US4 — defence in depth confirmation)
5. Phase 6 → Smoke-test hardened (all four user stories falsifiably validated)

---

## Notes

- [P] tasks involve different files or independent sections — safe to run in parallel
- All seed-kong.sh tasks (T005–T012) write to the same file — run sequentially
- `kong migrations bootstrap` is idempotent: the `|| true` guard prevents failure on re-runs
- Key-auth is applied at **service level** (not global) so the health service can remain unauthenticated without the anonymous-consumer pattern (see `quickstart.md` Option A rationale)
- `SMOKE_API_KEY` must be set in `.env` before running `make seed-kong`; the script fails fast with a clear error if it is unset
- After implementing T004, the existing `services/kong/kong.yml` is no longer loaded by Kong; it is retained in the repo as a historical reference only
