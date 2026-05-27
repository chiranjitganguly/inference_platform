---
description: "Task list for Developer Environment Setup Script"
---

# Tasks: Developer Environment Setup Script

**Input**: Design documents from `specs/001-dev-env-setup/`

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/script-interface.md ✅

**Tests**: No automated test framework — validation is manual execution per `quickstart.md` scenarios.

**Target file**: `scripts/setup-mac.sh` (single executable Bash script)

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (independent of other in-progress tasks)
- **[Story]**: Which user story this task belongs to (US1–US4)

---

## Phase 1: Setup

**Purpose**: Create the script file and supporting template.

- [x] T001 Create `scripts/` directory and `scripts/setup-mac.sh` with `#!/usr/bin/env bash` shebang and `set -euo pipefail` as the only initial content
- [x] T002 Make `scripts/setup-mac.sh` executable: `chmod 755 scripts/setup-mac.sh`
- [x] T003 [P] Create `.env.example` at project root with all required platform variable names and strictly empty values — include: `LITELLM_MASTER_KEY=`, `POSTGRES_PASSWORD=`, `POSTGRES_DB=`, `REDIS_PASSWORD=`, `OPENAI_API_KEY=`, `ANTHROPIC_API_KEY=`, `GOOGLE_API_KEY=`, `COHERE_API_KEY=`, `LANGFUSE_SECRET_KEY=`, `LANGFUSE_PUBLIC_KEY=`, `KEYCLOAK_ADMIN_PASSWORD=`, `VAULT_ROOT_TOKEN=`, `OPA_API_KEY=`

**Checkpoint**: `bash scripts/setup-mac.sh` exits with error (empty script aside from strict mode), `.env.example` exists with all empty values.

---

## Phase 2: Foundational

**Purpose**: Shared output infrastructure and hard prerequisites that block all user stories.

⚠️ All Phase 3–6 tasks depend on this phase being complete.

- [x] T004 Add tagged output helper functions to `scripts/setup-mac.sh`: `ok()` prints `[OK]     `, `info()` prints `[INFO]   `, `warn()` prints `[WARNING]`, `err()` prints `[ERROR]  ` — each followed by its argument on stdout; `err()` does not exit (caller decides)
- [x] T005 Add platform guard to `scripts/setup-mac.sh`: check `uname -s` equals `Darwin`; if not, call `err` with "This script requires macOS." and `exit 1`
- [x] T006 [P] Add macOS version gate to `scripts/setup-mac.sh`: read `sw_vers -productVersion`, extract major version with `cut -d. -f1`; if major < 14 call `err "macOS 14.0+ required. Detected: $os_version"` and `exit 1`

**Checkpoint**: Running script on Linux prints `[ERROR]  This script requires macOS.` and exits 1. Running on macOS 13 prints version error and exits 1.

---

## Phase 3: User Story 1 — First-Time Happy Path (Priority: P1) 🎯 MVP

**Goal**: Developer on a correctly configured Mac runs the script and gets a `.env` file with exit code 0.

**Independent Test**: On a Mac with OrbStack running, ≥ 3000 MB free memory, no `.env` present, run `bash scripts/setup-mac.sh`; confirm exit code 0 and `ls .env` succeeds.

### Implementation for User Story 1

- [x] T007 [US1] Add `check_orbstack_installed()` to `scripts/setup-mac.sh`: use `command -v orbctl &>/dev/null`; on success call `ok "OrbStack is installed."`; on failure call `err "OrbStack is not installed."`, print `"        Install with: brew install --cask orbstack"`, and `exit 1`
- [x] T008 [US1] Add `check_orbstack_running()` to `scripts/setup-mac.sh`: use `orbctl status &>/dev/null 2>&1`; on success call `ok "OrbStack is running."`; on failure call `err "OrbStack is installed but not running."`, print `"        Open OrbStack.app to start it."`, and `exit 1`
- [x] T009 [US1] Add `check_memory()` to `scripts/setup-mac.sh`: compute `page_size=$(sysctl -n hw.pagesize)`, parse `free_pages` from `vm_stat | awk '/^Pages free:/ { gsub(/\.$/, "", $3); print $3 }'`, compute `free_mb=$(( free_pages * page_size / 1048576 ))`; if `free_mb >= 3000` call `ok "Free memory: ${free_mb} MB (threshold: 3000 MB)."`; if `free_mb < 3000` call `warn "Free memory: ${free_mb} MB — below recommended 3000 MB."` and print advisory line, then continue (no exit)
- [x] T010 [P] [US1] Add `.env.example` validation to `scripts/setup-mac.sh`: first check `[[ -f .env.example ]]`; if missing call `err ".env.example not found at project root."` and `exit 1`; then pipe through `grep -vE '^\s*#|^\s*$' .env.example | grep -qE '^[A-Za-z_][A-Za-z0-9_]*=.+'`; if match found call `err ".env.example contains non-empty values — remove all secrets before committing."` and `exit 1`; on pass call `ok ".env.example validated — no secrets detected."`
- [x] T011 [US1] Add `.gitignore` guard to `scripts/setup-mac.sh`: use `grep -qxF '.env' .gitignore 2>/dev/null`; if absent call `info "Adding .env to .gitignore..."` and `echo ".env" >> .gitignore`; if already present call `ok ".env already in .gitignore."`
- [x] T012 [US1] Add `.env` creation to `scripts/setup-mac.sh`: check `[[ -f .env ]]`; if exists call `info ".env already exists — skipping creation."` and continue; if absent run `cp .env.example .env`; if `cp` fails call `err "Failed to create .env (check directory permissions)."` and `exit 1`; on success call `ok "Created .env from .env.example."`
- [x] T013 [US1] Add success message block to `scripts/setup-mac.sh`: print blank line, `"Setup complete. Fill in .env with your credentials, then run:"`, and `"  docker compose --profile core up -d"` — print only after all checks pass
- [x] T014 [US1] Add `main()` function to `scripts/setup-mac.sh` that calls in order: `check_platform` (T005 guard), `check_macos_version` (T006 gate), `check_orbstack_installed` (T007), `check_orbstack_running` (T008), `check_memory` (T009), `validate_env_example` (T010), `guard_gitignore` (T011), `create_env_file` (T012), success block (T013); add `main "$@"` as last line of script

**Checkpoint**: `bash scripts/setup-mac.sh` on correctly configured Mac produces tagged output for all checks, creates `.env`, exits 0. Re-running prints `[INFO]   .env already exists` and exits 0.

---

## Phase 4: User Story 2 — Low Memory Warning (Priority: P2)

**Goal**: Script warns but does not block when free memory < 3000 MB.

**Independent Test**: Temporarily edit `check_memory()` to use a threshold of `999999` MB, run the script; confirm `[WARNING]` is printed, `.env` is created, exit code is 0.

### Implementation for User Story 2

- [x] T015 [US2] Verify warning branch in `check_memory()` in `scripts/setup-mac.sh`: confirm warning message uses `[WARNING]` prefix tag (matching `warn()` helper from T004), includes the numeric MB value and the 3000 MB reference, and that execution proceeds past the warning — validate using the temporary-threshold technique per `specs/001-dev-env-setup/quickstart.md`

**Checkpoint**: Under low-memory simulation, script outputs `[WARNING] Free memory: X MB — below recommended 3000 MB.` on a dedicated line, continues through all remaining checks, creates `.env`, and exits 0.

---

## Phase 5: User Story 3 — OrbStack Not Installed (Priority: P3)

**Goal**: Script exits 1 with `brew install --cask orbstack` instruction when `orbctl` is absent.

**Independent Test**: Temporarily rename `orbctl` out of PATH (`PATH="" bash scripts/setup-mac.sh`), run script; confirm `brew install --cask orbstack` appears in output and exit code is 1.

### Implementation for User Story 3

- [x] T016 [US3] Verify `check_orbstack_installed()` error path in `scripts/setup-mac.sh`: confirm output contains `[ERROR]` tag, the literal string `brew install --cask orbstack`, and that no `.env` file is created — use `PATH=/usr/bin:/bin bash scripts/setup-mac.sh` to simulate absent `orbctl`

**Checkpoint**: `PATH=/usr/bin:/bin bash scripts/setup-mac.sh` outputs `[ERROR]  OrbStack is not installed.` followed by brew install instruction, exits 1, and `.env` is absent.

---

## Phase 6: User Story 4 — OrbStack Not Running (Priority: P4)

**Goal**: Script exits 1 with start instruction when `orbctl status` fails.

**Independent Test**: Quit OrbStack.app, then run `bash scripts/setup-mac.sh`; confirm "start OrbStack" instruction appears and exit code is 1.

### Implementation for User Story 4

- [ ] T017 [US4] Verify `check_orbstack_running()` error path in `scripts/setup-mac.sh`: confirm output contains `[ERROR]` tag, an instruction to open or start OrbStack.app, and that no `.env` file is created — validate by quitting OrbStack.app and running the script

**Checkpoint**: With OrbStack installed but not running, script outputs `[ERROR]  OrbStack is installed but not running.` followed by start instruction, exits 1, and `.env` is absent.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Safety validations across all user stories and idempotency confirmation.

- [ ] T018 [P] Validate no `.env` creation on any hard-failure path: run each failure scenario (US3, US4, missing `.env.example`, secrets in `.env.example`) in a temp directory; confirm `[[ ! -f .env ]]` after each run — covers SC-004 from spec
- [ ] T019 [P] Validate idempotency: run `bash scripts/setup-mac.sh` twice on a clean environment; confirm second run prints `[INFO]   .env already exists — skipping creation.`, does not modify `.env`, and exits 0 — confirm `.gitignore` is not duplicated
- [ ] T020 Validate complete output format against `specs/001-dev-env-setup/contracts/script-interface.md`: run each user story scenario and compare actual output tags and messages against the documented examples in the contract

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 completion
- **Phase 3 (US1)**: Depends on Phase 2 — T007–T014 must be done in order (T007 before T008, T010 and T011 can be parallel, T014 last)
- **Phase 4 (US2)**: Depends on T009 (memory check) being complete
- **Phase 5 (US3)**: Depends on T007 (install check) being complete
- **Phase 6 (US4)**: Depends on T008 (running check) being complete
- **Phase 7 (Polish)**: Depends on all user story phases complete

### Within Phase 3 (US1) — Internal Order

```
T007 (orbstack installed check)
  └── T008 (orbstack running check)
        └── T009 (memory check)
              ├── T010 [P] (.env.example validation)
              └── T011 (.gitignore guard)
                    └── T012 (.env creation)
                          └── T013 (success message)
                                └── T014 (main() wiring)
```

T010 and T011 can be implemented in parallel (different logical sections of the script), but both must complete before T012.

### Parallel Opportunities

```bash
# Phase 1 — T002 and T003 can run in parallel after T001:
Task: "chmod 755 scripts/setup-mac.sh"
Task: "Create .env.example with empty variable names"

# Phase 2 — T005 and T006 can run in parallel after T004:
Task: "Add platform guard (Darwin check)"
Task: "Add macOS version gate (sw_vers)"

# Phase 3 — T010 and T011 can run in parallel after T009:
Task: "Add .env.example secret validation"
Task: "Add .gitignore guard"

# Phase 7 — T018, T019, T020 can all run in parallel:
Task: "Validate no .env on hard failure"
Task: "Validate idempotency"
Task: "Validate output format vs contract"
```

---

## Implementation Strategy

### MVP (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1 (T007–T014 in dependency order)
4. **STOP and VALIDATE**: Run `bash scripts/setup-mac.sh` on clean Mac → confirm exit 0, `.env` created
5. All other phases add error-path coverage and safety validation

### Incremental Delivery

1. Phase 1 + 2 → Script has platform gate and strict mode skeleton
2. Phase 3 → Full happy path works (MVP deliverable)
3. Phase 4 → Memory warning path confirmed
4. Phase 5 → OrbStack not-installed path confirmed
5. Phase 6 → OrbStack not-running path confirmed
6. Phase 7 → All acceptance criteria from spec verified

---

## Notes

- All tasks write to a single file: `scripts/setup-mac.sh`. There are no cross-file conflicts.
- Validation tasks (T015–T020) do not modify the script — they confirm behavior.
- `[P]` tasks within the same phase can be implemented concurrently by different developers.
- The `.env.example` created in T003 must be committed to source control; `.env` must never be.
- Reference `specs/001-dev-env-setup/contracts/script-interface.md` for exact expected output format and exit codes during all validation tasks.
