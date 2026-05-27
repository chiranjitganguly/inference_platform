# Implementation Plan: Developer Environment Setup Script

**Branch**: `001-dev-env-setup` | **Date**: 2026-05-26 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-dev-env-setup/spec.md`

## Summary

A single Bash script (`scripts/setup-mac.sh`) that validates a macOS 14+ developer
environment for the AI Inference Platform. It checks OrbStack presence and running state
via `orbctl`, measures free RAM via `vm_stat` (3000 MB threshold), ensures `.env` is
listed in `.gitignore`, validates `.env.example` contains no secrets, then copies
`.env.example` → `.env`. Exits 0 on success; non-zero on hard failures (OrbStack absent
or stopped, template missing, secrets detected in template). Low-memory warnings are soft
— setup continues and exits 0.

## Technical Context

**Language/Version**: Bash 5.x — ships with macOS 14+; no additional install required

**Primary Dependencies**:

| Tool | Source | Purpose |
|---|---|---|
| `orbctl` | Bundled with OrbStack.app | Detect OrbStack install and running state |
| `vm_stat` | macOS built-in | Read free memory page count |
| `sysctl` | macOS built-in | Read hardware page size |
| `sw_vers` | macOS built-in | Verify macOS 14+ version gate |
| `cp`, `grep`, `awk` | POSIX (always present) | File operations and parsing |

**Storage**: File system only — reads `.env.example`; writes `.env`; conditionally appends
to `.gitignore`

**Testing**: Manual execution across the 4 user story scenarios on macOS 14+. No automated
test runner is required for a script of this scope.

**Target Platform**: macOS 14.0+ (Sonoma) with OrbStack installed as the Docker runtime

**Project Type**: CLI setup script — single executable file, no arguments required

**Performance Goals**: Full script execution < 30 seconds under all paths (spec allows 5 min)

**Constraints**:
- Pure Bash only — no Python, Node.js, Ruby, Go, or any interpreted runtime
- No package manager invocations within the script
- No `sudo` or elevated privileges
- Idempotent: safe to run multiple times without side effects

**Scale/Scope**: Single developer workstation; designed for first-run with re-run safety

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design — see below.*

| Principle | Relevance | Status |
|---|---|---|
| I. Request Flow Integrity | N/A — script does not handle inference requests | ✅ Not applicable |
| II. Prompt Content Ephemeral | N/A — no prompt or response data involved | ✅ Not applicable |
| III. OpenAI API Compatibility | N/A — not an API endpoint | ✅ Not applicable |
| IV. Defence in Depth | **Directly enforced** — script validates §5.1 (zero plaintext secrets): checks `.env.example` for non-empty values (FR-014) and ensures `.env` is in `.gitignore` (FR-013) | ✅ PASS |
| V. Falsifiable Acceptance Criteria | All SC entries verified by `exit code`, file existence, or stdout content | ✅ PASS |

**Additional constitution checks:**

- **§5.1 Zero plaintext secrets**: Script explicitly guards against secrets in `.env.example`
  and protects `.env` from source control. ✅
- **§10.4 Configuration via env vars**: Script creates the `.env` file that enables env var
  configuration for all platform services. ✅
- **§9.1 Local first**: This script is the entry point for the local development environment. ✅
- **§11 Falsifiable AC**: Every success criterion in spec.md uses exit codes, file existence
  checks, or stdout string matching — all deterministic. ✅

**Gate result: PASS** — no violations. No Complexity Tracking required.

**Post-Phase-1 re-check**: ✅ PASS — design introduces no new principle tensions.

---

## Project Structure

### Documentation (this feature)

```text
specs/001-dev-env-setup/
├── plan.md              # This file
├── research.md          # Phase 0 — tool API decisions
├── data-model.md        # Phase 1 — file entity definitions
├── quickstart.md        # Phase 1 — developer usage guide
├── contracts/
│   └── script-interface.md   # Phase 1 — exit codes, output contract
├── checklists/
│   └── requirements.md       # Spec quality checklist
└── tasks.md             # Phase 2 output (via /speckit-tasks)
```

### Source Code (repository root)

```text
scripts/
└── setup-mac.sh         # The setup script (executable, 755)

.env.example             # Template — empty values only, committed to repo
.env                     # Developer-local config — gitignored, never committed
.gitignore               # Must contain .env entry (enforced by setup-mac.sh)
```

**Structure Decision**: Single-file CLI script at `scripts/setup-mac.sh`. No `src/` or
`tests/` hierarchy — the script is self-contained. The `scripts/` directory will hold all
platform operational scripts as the project grows.
