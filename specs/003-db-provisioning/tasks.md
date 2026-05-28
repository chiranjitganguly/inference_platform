# Tasks: Automated Database Provisioning

**Input**: Design documents from `specs/003-db-provisioning/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | quickstart.md ✅

**Tests**: Not requested — no test tasks generated.

**Deliverables**: Two files — `scripts/init-db.sql` and `docker-compose.yml`.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no incomplete-task dependencies)
- **[Story]**: User story this task belongs to (US1, US2, US3)

---

## Phase 1: Setup

**Purpose**: Create the SQL init script stub so subsequent tasks have a file to write into.

- [x] T001 Create `scripts/init-db.sql` with a header comment block: `-- AI Inference Platform — PostgreSQL database provisioning`, purpose sentence, and list of the six database names this script provisions

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Write all content into both files. No user story verification can begin until both files are complete and syntactically correct.

**⚠️ CRITICAL**: Tasks T002 and T004 write to different files and can run in parallel. T003 must follow T002 (same file). T005 must follow T004 (same file).

- [x] T002 [P] Write 6 conditional `CREATE DATABASE` blocks in `scripts/init-db.sql` — one per database (`litellm`, `keycloak`, `mlflow`, `kong`, `phoenix`, `langfuse`) using the `\gexec` + `NOT EXISTS` guard pattern from research.md Decision 2: `SELECT format('CREATE DATABASE %I', '<name>') WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '<name>')\gexec`
- [x] T003 Write 6 `GRANT ALL PRIVILEGES ON DATABASE <name> TO CURRENT_USER;` statements in `scripts/init-db.sql` — one per database in the same order as the CREATE blocks (research.md Decision 3)
- [x] T004 [P] Create `docker-compose.yml` with the `postgres` service definition: image `postgres:16-alpine`, profile `core`, environment block with `POSTGRES_USER: ${POSTGRES_USER}` / `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}` / `POSTGRES_DB: ${POSTGRES_DB}` (no literal values), volume mount `pg_data:/var/lib/postgresql/data`, read-only bind-mount `./scripts/init-db.sql:/docker-entrypoint-initdb.d/init.sql:ro`, and `pg_isready` healthcheck as: `test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]`, `interval: 5s`, `timeout: 5s`, `retries: 10`, `start_period: 10s` — all per research.md Decisions 4 and 5
- [x] T005 Add top-level `volumes:` key with `pg_data:` (empty mapping) to `docker-compose.yml` so Docker creates the named volume on first `docker compose up`

**Checkpoint**: Both files are complete. Run `docker compose --env-file .env config` to confirm YAML is valid before proceeding.

---

## Phase 3: User Story 1 — First-Time Platform Setup (Priority: P1) 🎯 MVP

**Goal**: On a fresh `pg_data` volume all six databases are present and the postgres service is healthy within 30 seconds of starting — with zero manual steps.

**Independent Test**: Wipe volume, start core profile, run AC-1 and AC-4 from research.md acceptance criteria.

- [ ] T006 [US1] Verify fresh-volume provisioning: run `make down-v && make up-core`, wait for postgres to reach healthy status, then execute acceptance criterion AC-1 (`psql -lqt | awk '{print $1}' | grep -E '^(litellm|keycloak|mlflow|kong|phoenix|langfuse)$' | wc -l | grep -q 6`) — confirm output is `PASS` for all six databases
- [ ] T007 [US1] Verify postgres healthcheck: execute acceptance criterion AC-4 (`docker inspect … --format '{{.State.Health.Status}}'`) — confirm output is `healthy`

**Checkpoint**: User Story 1 fully verified. Fresh-volume provisioning works and postgres is healthy. MVP deliverable complete.

---

## Phase 4: User Story 2 — Idempotent Restart (Priority: P2)

**Goal**: A `make down && make up-core` against the existing `pg_data` volume produces zero errors and all six databases remain present.

**Independent Test**: Restart against the volume from Phase 3 checkpoint — no data loss, no provisioning errors in logs.

- [ ] T008 [US2] Verify idempotent restart: run `make down && make up-core` against the existing volume (do NOT run `make down-v`), then execute acceptance criterion AC-3 — confirm all 6 databases still present and `make logs svc=postgres | grep -iE "error|fatal"` returns no matches

**Checkpoint**: User Story 2 verified. Container restarts are safe.

---

## Phase 5: User Story 3 — Privilege Verification (Priority: P3)

**Goal**: The platform user can CREATE and DROP tables in each of the six databases, confirming full privileges are in effect.

**Independent Test**: Execute the table-probe loop (AC-2) against all six databases — all must return success.

- [ ] T009 [US3] Verify platform user privileges: execute acceptance criterion AC-2 for each of the six databases — for each database run `psql -U "$POSTGRES_USER" -d "<db>" -c "CREATE TABLE IF NOT EXISTS _probe (id int); DROP TABLE _probe;"` and confirm all six return success with no permission errors

**Checkpoint**: All three user stories verified. Feature is complete.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Memory budget validation and a forward-compatibility note in docker-compose.yml for future services.

- [ ] T010 [P] Verify memory budget: run `make stats` with only the core profile active and confirm the postgres container is within the core-profile ~620 MB total budget per constitution §7.4 (acceptance criterion AC-5)
- [x] T011 [P] Add a comment block at the bottom of `docker-compose.yml` showing the `depends_on` snippet that all future services must use to wait for postgres: `# Future services that require postgres must include:\n# depends_on:\n#   postgres:\n#     condition: service_healthy`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on T001 — blocks all user story verification
- **Phase 3 (US1)**: Depends on Phase 2 completion — MVP gate
- **Phase 4 (US2)**: Depends on Phase 3 (uses the volume from the US1 run — do NOT wipe between phases)
- **Phase 5 (US3)**: Depends on Phase 2 completion — can run immediately after Phase 2 if Phase 3 volume is available
- **Phase 6 (Polish)**: Depends on Phases 3–5 completion

### User Story Dependencies

- **US1 (P1)**: Depends only on Phase 2
- **US2 (P2)**: Depends on US1 having been run (needs existing volume)
- **US3 (P3)**: Depends only on Phase 2 (table probe is stateless)

### Within Phase 2 (Parallel Opportunities)

```
T001 (setup)
  ├── T002 → T003    (init-db.sql — sequential, same file)
  └── T004 → T005    (docker-compose.yml — sequential, same file)
      ↑ T002 and T004 run in PARALLEL (different files)
```

---

## Parallel Example: Phase 2

```bash
# These two tasks can be run simultaneously by two agents:
Task A: "Write CREATE DATABASE + GRANT blocks in scripts/init-db.sql" (T002 → T003)
Task B: "Write postgres service + pg_data volume in docker-compose.yml"  (T004 → T005)
```

---

## Implementation Strategy

### MVP (User Story 1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002–T005)
3. Complete Phase 3: User Story 1 (T006–T007)
4. **STOP and validate**: both acceptance criteria return PASS
5. Two files delivered, platform can provision a fresh database layer

### Full Delivery (All Stories)

1. MVP above → confirms fresh provisioning
2. Phase 4 (US2): T008 → confirms restarts are safe
3. Phase 5 (US3): T009 → confirms privilege grants
4. Phase 6 (Polish): T010–T011 → memory budget + forward-compatibility note

---

## Notes

- `[P]` tasks touch different files and have no dependency on incomplete prior tasks
- `[Story]` labels map to spec.md user stories for traceability
- Do NOT run `make down-v` between Phase 3 and Phase 4 — US2 tests idempotency against the same volume
- No test tasks generated — not requested in spec
- Commit after Phase 2 checkpoint (both files syntactically valid) and after each user story phase
