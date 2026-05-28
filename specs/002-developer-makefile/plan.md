# Implementation Plan: Developer Makefile

**Branch**: `002-developer-makefile` | **Date**: 2026-05-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-developer-makefile/spec.md`

## Summary

A single GNU Makefile at the project root that wraps every Docker Compose operation as a
named target. Developers never type `docker compose` directly. All invocations go through a
`COMPOSE` variable that unconditionally applies `--env-file .env`. The six service-group
profiles (`core`, `obs`, `auth`, `safety`, `gov`, `portal`) each have a dedicated startup
target; all six are also started by `up-all`. `restart` and `logs` require `svc=<name>` and
exit non-zero with a usage error if it is omitted. `seed-kong` and `seed-vault` are
idempotent. A self-documenting `help` target (`##` comment + awk) is the default goal.

## Technical Context

**Language/Version**: GNU Make 3.81+ — ships with macOS Xcode Command Line Tools; no
additional install required

**Primary Dependencies**:

| Tool | Source | Purpose |
|---|---|---|
| `docker compose` | Bundled with OrbStack | All container lifecycle operations |
| `bash` | macOS built-in | Inline recipe shell |
| `scripts/seed-kong.sh` | This repository | Kong Admin API configuration |
| `scripts/seed-vault.sh` | This repository | Vault secret bootstrap |
| `scripts/smoke-test.sh` | This repository | End-to-end health probes |
| `awk` | POSIX (always present) | Help target output formatting |

**Storage**: Filesystem only — `Makefile` at project root reads `.env` (passed via
`--env-file`); no database or in-memory state

**Testing**: Manual execution of each target across the nominal path and the `svc=`-missing
error paths. No automated test runner is warranted for a Makefile of this scope.

**Target Platform**: macOS 14+ with OrbStack as the Docker Compose runtime

**Project Type**: Build tool / developer workflow orchestrator — single file at project root

**Performance Goals**: All error-path targets (missing `svc=`) respond in under 1 second.
All `up-<group>` targets exit within 30 seconds when images are already pulled. `make smoke`
completes in under 30 seconds on a running, seeded platform.

**Constraints**:
- GNU Make syntax only — no external scripting beyond the repo's `scripts/` files
- `COMPOSE` variable MUST be defined once and used by every Docker Compose recipe
- `restart` and `logs` MUST use `ifndef svc` / `$(error ...)` guard — no default fallback
- `.PHONY` declarations REQUIRED for every target (prevents file-name collisions)
- Idempotent seed targets — recipes invoke existing `scripts/` wrappers that handle upsert logic
- `down-v` target added (not in spec) to support volume teardown during environment reset

**Scale/Scope**: Single file consumed by all developers on every platform interaction

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design — see below.*

| Principle | Relevance | Status |
|---|---|---|
| I. Request Flow Integrity | **Indirect** — Makefile starts containers in the correct topology (Kong before Guardrails before LiteLLM). No target short-circuits the chain or exposes LiteLLM :4000 as a host port. | ✅ PASS |
| II. Prompt Content Ephemeral | N/A — Makefile contains no prompt or response data | ✅ Not applicable |
| III. OpenAI API Compatibility | N/A — Makefile is not an API endpoint | ✅ Not applicable |
| IV. Defence in Depth | **Directly enforced** — `COMPOSE --env-file .env` ensures secrets are always loaded from the gitignored `.env` file, never hardcoded. `seed-vault` bootstraps Vault so credentials leave `.env` at runtime. The Makefile never embeds credential values. | ✅ PASS |
| V. Falsifiable Acceptance Criteria | All SC entries verified by exit code (`$?`), `docker ps` output, or stdout content — all deterministic | ✅ PASS |

**Additional constitution checks:**
- **§5.1 Zero plaintext secrets**: `COMPOSE = docker compose --env-file .env` — values come from
  the gitignored `.env` file, never from the Makefile itself. ✅
- **§9.1 Local first**: This Makefile is the primary entry point for the local development
  workflow, layered on top of `setup-mac.sh`. ✅
- **§11 Falsifiable AC**: Every success criterion uses exit codes, container state, or timed
  stdout matching — all deterministic. ✅

**Gate result: PASS** — no violations. No Complexity Tracking required.

**Post-Phase-1 re-check**: ✅ PASS — design introduces no new principle tensions.

---

## Project Structure

### Documentation (this feature)

```text
specs/002-developer-makefile/
├── plan.md                        # This file
├── research.md                    # Phase 0 — Make pattern decisions
├── data-model.md                  # Phase 1 — file entity definitions
├── quickstart.md                  # Phase 1 — developer usage guide
├── contracts/
│   └── makefile-interface.md      # Phase 1 — target interface contract
├── checklists/
│   └── requirements.md            # Spec quality checklist
└── tasks.md                       # Phase 2 output (via /speckit-tasks)
```

### Source Code (repository root)

```text
Makefile                   # The only deliverable — project root

scripts/
├── seed-kong.sh           # Invoked by make seed-kong (must be idempotent)
├── seed-vault.sh          # Invoked by make seed-vault (must be idempotent)
└── smoke-test.sh          # Invoked by make smoke

.env.example               # Variable names committed; values in .env (gitignored)
.env                       # Passed via --env-file by every COMPOSE invocation
docker-compose.yml         # Defines all six profiles and services
```

**Structure Decision**: Single-file deliverable at project root. No `src/` hierarchy — the
Makefile is self-contained. The `scripts/` directory already exists and holds the seed and
smoke scripts that the Makefile invokes.
