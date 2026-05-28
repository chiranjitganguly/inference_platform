# Tasks: Developer Makefile

**Input**: Design documents from `specs/002-developer-makefile/`

**Feature**: GNU Makefile at project root wrapping all Docker Compose operations. Single
deliverable file with 17 named targets. All Compose invocations via `COMPOSE` variable.
`restart` and `logs` require `svc=`. Seed targets are idempotent. `help` is the default goal.

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Independent of concurrent tasks (no file-write conflict)
- **[Story]**: User story this task belongs to
- All tasks modify `Makefile` (sequential within phases) or independent script files ([P])

---

## Phase 1: Setup

**Purpose**: Create the Makefile skeleton with all shared infrastructure that every target
depends on.

- [x] T001 Create `Makefile` at project root with `COMPOSE := docker compose --env-file .env`, `.DEFAULT_GOAL := help`, and `.PHONY` declaration listing all 17 targets: `help up-core up-obs up-auth up-safety up-gov up-portal up-all down down-v restart logs ps stats smoke seed-kong seed-vault`
- [x] T002 Add `help` target to `Makefile` using the awk self-documentation pattern: `@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)` with `## Show available targets` comment

**Checkpoint**: `make help` prints the usage header and exits 0. No other targets yet.

---

## Phase 2: Foundational

**Purpose**: No additional blocking prerequisites — the `COMPOSE` variable and `.PHONY`
declarations from Phase 1 are the only shared foundation for a single-file deliverable.

**⚠️ CRITICAL**: Phase 1 must be complete before any user story phase begins.

---

## Phase 3: User Story 1 — Start a Service Group (Priority: P1) 🎯 MVP

**Goal**: Every `up-<group>`, `up-all`, `down`, and `down-v` target works correctly, loads
`.env` automatically, and leaves other running profiles unaffected.

**Independent Test**:
```bash
make up-core                        # → exits 0, core containers running
docker ps | grep -c Up              # → count matches core profile service count
make down                           # → exits 0
make down                           # → exits 0 (idempotent)
make up-core && make up-obs         # → both profile containers running simultaneously
make down-v                         # → exits 0, volumes removed
```

- [x] T003 [US1] Add `up-core` target to `Makefile`: `$(COMPOSE) --profile core up -d` with `## Start core profile (LiteLLM, Kong, Redis×2, Postgres, Prometheus, Grafana)` comment
- [x] T004 [US1] Add `up-obs` target to `Makefile`: `$(COMPOSE) --profile obs up -d` with `## Start obs profile (Loki, OTel, Alertmanager, Jaeger, Phoenix Arize, Langfuse)` comment
- [x] T005 [US1] Add `up-auth` target to `Makefile`: `$(COMPOSE) --profile auth up -d` with `## Start auth profile (Keycloak, OPA, Vault)` comment
- [x] T006 [US1] Add `up-safety` target to `Makefile`: `$(COMPOSE) --profile safety up -d` with `## Start safety profile (Presidio, LLM Guard, Guardrails)` comment
- [x] T007 [US1] Add `up-gov` target to `Makefile`: `$(COMPOSE) --profile gov up -d` with `## Start gov profile (MLflow)` comment
- [x] T008 [US1] Add `up-portal` target to `Makefile`: `$(COMPOSE) --profile portal up -d` with `## Start portal profile (Swagger UI, Portal backend, Platform UI)` comment
- [x] T009 [US1] Add `up-all` target to `Makefile`: single `$(COMPOSE) --profile core --profile obs --profile auth --profile safety --profile gov --profile portal up -d` invocation with `## Start all profiles` comment
- [x] T010 [US1] Add `down` target to `Makefile`: `$(COMPOSE) down` with `## Stop all containers (volumes preserved)` comment
- [x] T011 [US1] Add `down-v` target to `Makefile`: `$(COMPOSE) down -v` with `## Stop all containers and remove volumes (destructive — re-seed after)` comment

**Checkpoint**: All six `up-<group>` targets, `up-all`, `down`, and `down-v` work. Running
`up-core` then `up-obs` leaves both profiles active. `make help` lists all 11 new targets.

---

## Phase 4: User Story 2 — Restart a Named Service (Priority: P2)

**Goal**: `make restart svc=<name>` restarts exactly one container. `make restart` alone
exits non-zero with a usage error and touches nothing.

**Independent Test**:
```bash
make restart              # → exit 2, stderr: "Usage: make restart svc=<service-name>"
make restart svc=kong     # → exits 0, only kong container restarted
docker ps | grep kong     # → Up (restarted) — all other containers unchanged
```

- [x] T012 [US2] Add `restart` target to `Makefile` with `ifndef svc` guard: `$(error Usage: make restart svc=<service-name>)` on missing `svc=`, and `$(COMPOSE) restart $(svc)` recipe with `## Restart a service: make restart svc=<name>` comment

**Checkpoint**: `make restart` exits 2 with usage message; `make restart svc=litellm` restarts
only litellm. `make help` lists the restart target.

---

## Phase 5: User Story 3 — Tail Service Logs (Priority: P2)

**Goal**: `make logs svc=<name>` streams live logs. `make logs` alone exits non-zero with a
usage error and produces no output.

**Independent Test**:
```bash
make logs               # → exit 2, stderr: "Usage: make logs svc=<service-name>"
make logs svc=kong      # → streams kong log output, does not exit until Ctrl-C
```

- [x] T013 [US3] Add `logs` target to `Makefile` with `ifndef svc` guard: `$(error Usage: make logs svc=<service-name>)` on missing `svc=`, and `$(COMPOSE) logs -f $(svc)` recipe with `## Tail logs for a service: make logs svc=<name>` comment

**Checkpoint**: `make logs` exits 2 with usage message; `make logs svc=kong` streams logs.
`make help` lists the logs target.

---

## Phase 6: User Story 4 — Seed Third-Party Services (Priority: P3)

**Goal**: `make seed-kong` and `make seed-vault` apply configuration idempotently. Re-running
either target does not duplicate routes, services, or secrets.

**Independent Test**:
```bash
make seed-kong               # → exits 0
make seed-kong               # → exits 0 again (idempotent — no duplicate routes)
curl -s http://localhost:8001/services | jq '.data | length'  # → same count both times
make seed-vault              # → exits 0
make seed-vault              # → exits 0 again (idempotent — vault kv put overwrites)
```

- [x] T014 [P] [US4] Audit `scripts/seed-kong.sh` — ensure every Kong entity is created/updated via `PUT /services/{name}` and `PUT /routes/{name}` (upsert semantics); replace any bare `POST` without a name field; verify script exits non-zero on Kong API errors
- [x] T015 [P] [US4] Audit `scripts/seed-vault.sh` — ensure every secret uses `vault kv put <path> key=value` (overwrite semantics); verify script exits non-zero on Vault errors and handles the case where Vault is not yet unsealed with a clear error message
- [x] T016 [US4] Add `seed-kong` target to `Makefile`: `bash scripts/seed-kong.sh` with `## Seed Kong routes and services (idempotent)` comment
- [x] T017 [US4] Add `seed-vault` target to `Makefile`: `bash scripts/seed-vault.sh` with `## Seed Vault secrets (idempotent)` comment

**Checkpoint**: `make seed-kong` and `make seed-vault` each exit 0. Running each twice
produces no duplicates. `make help` lists both seed targets.

---

## Phase 7: User Story 5 — Run Smoke Tests (Priority: P3)

**Goal**: `make smoke` runs end-to-end health probes and exits 0 if all pass, non-zero with
specific failure details if any fail. Completes in under 30 seconds.

**Independent Test**:
```bash
# With platform running and seeded:
make smoke          # → exits 0, all checks pass, output shows each probe result
# With kong stopped:
make down && make up-obs   # (obs only — no kong)
make smoke          # → exits non-zero, output identifies the failing probe
```

- [x] T018 [US5] Audit `scripts/smoke-test.sh` — verify it probes Kong `:8080` health endpoint and at least one LLM proxy endpoint; ensure it prints a labelled pass/fail line per probe and exits non-zero if any probe fails; update to match current platform routes if needed
- [x] T019 [US5] Add `smoke` target to `Makefile`: `bash scripts/smoke-test.sh` with `## Run smoke tests against Kong :8080 (exit 0 = pass)` comment

**Checkpoint**: `make smoke` exits 0 on a running seeded platform; exits non-zero with a
specific message when a service is down. Completes in under 30 seconds. `make help` lists it.

---

## Phase 8: User Story 6 — Observe Platform Status (Priority: P4)

**Goal**: `make ps` lists containers with ports and status. `make stats` streams live
per-container CPU and memory until Ctrl-C.

**Independent Test**:
```bash
make ps      # → prints Compose ps table, exits 0
make stats   # → streams live docker stats output, exits on Ctrl-C
```

- [x] T020 [US6] Add `ps` target to `Makefile`: `$(COMPOSE) ps` with `## Show running containers with ports and status` comment
- [x] T021 [US6] Add `stats` target to `Makefile`: `docker stats` with `## Live per-container CPU and memory (Ctrl-C to exit)` comment

**Checkpoint**: `make ps` lists containers; `make stats` streams live metrics. Both listed
in `make help`.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Final verification that the Makefile is complete, self-consistent, and matches
the contract defined in `specs/002-developer-makefile/contracts/makefile-interface.md`.

- [x] T022 Verify `.PHONY` line in `Makefile` lists all 17 targets exactly: `help up-core up-obs up-auth up-safety up-gov up-portal up-all down down-v restart logs ps stats smoke seed-kong seed-vault` — add any missing entries
- [x] T023 Run `make help` and verify all 17 targets appear in the output with non-empty one-line descriptions; fix any target missing a `##` comment
- [x] T024 Verify `COMPOSE` variable is used in every recipe that invokes `docker compose` — grep `Makefile` for bare `docker compose` not preceded by `$(COMPOSE)` and replace
- [ ] T025 Run quickstart.md first-run sequence: `make up-core && make seed-kong && make seed-vault && make smoke` — confirm exits 0 end-to-end (manual — requires running platform)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: N/A for single-file deliverable
- **User Stories (Phase 3–8)**: Depend on Phase 1; can proceed in strict priority order
  (P1 → P2 → P3 → P4) because all tasks modify the same `Makefile`
- **Script audits T014, T015, T018** (`[P]`): Can run in parallel with Makefile target
  additions for the same phase — different files
- **Polish (Phase 9)**: Depends on all user story phases complete

### User Story Dependencies

- **US1 (P1)**: After Phase 1 — no other story dependency
- **US2 (P2)**: After Phase 1 — independent of US1
- **US3 (P2)**: After Phase 1 — independent of US1, US2
- **US4 (P3)**: After Phase 1; script audit tasks (T014, T015) independent of Makefile edits
- **US5 (P3)**: After Phase 1; script audit task (T018) independent of Makefile edits
- **US6 (P4)**: After Phase 1 — independent of all other stories

### Parallel Opportunities (within each phase)

- **T014, T015** (US4 script audits): run in parallel — different script files
- **T014/T015 vs T016/T017**: script audits can run while Makefile targets are being added
- **T018 vs T019**: script audit can run while Makefile smoke target is being added
- **T020 vs T021** (US6): can run in parallel if two sessions — different Makefile blocks

---

## Parallel Example: Phase 6 (US4 — Seed)

```bash
# In parallel:
Task T014: "Audit scripts/seed-kong.sh for PUT upsert semantics"
Task T015: "Audit scripts/seed-vault.sh for vault kv put semantics"

# After both complete:
Task T016: "Add seed-kong target to Makefile"
Task T017: "Add seed-vault target to Makefile"
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1 (T001–T002) — Makefile skeleton + help
2. Complete Phase 3 (T003–T011) — all startup/teardown targets
3. **STOP and VALIDATE**: `make up-core` starts platform; `make down` tears it down; `make help` lists all 11 targets
4. The platform is operational — all other stories add workflow convenience

### Incremental Delivery

1. Phase 1 + US1 → Platform starts and stops ✅
2. + US2 → Developers can restart individual services ✅
3. + US3 → Developers can tail logs ✅
4. + US4 → First-run seeding workflow complete ✅
5. + US5 → Smoke test gate available for PRs ✅
6. + US6 → Full status observability ✅
7. Phase 9 → Verified, polished, production-ready ✅

---

## Notes

- All Makefile target additions (T003–T021 excluding [P] script tasks) are sequential edits
  to the same file — they are not file-conflict-safe to parallelise
- Script audit tasks (T014, T015, T018) are [P] because they operate on separate files
- Every task produces a `make help` that shows the new target — validate after each addition
- `$(error ...)` in Make prints to stderr and exits 2 (not 1) — this is correct per contract
- `docker stats` is invoked directly (not via `$(COMPOSE)`) per research Decision 8
