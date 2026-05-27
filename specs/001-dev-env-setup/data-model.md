# Data Model: Developer Environment Setup Script

**Feature**: `001-dev-env-setup`
**Date**: 2026-05-26

---

## Overview

This feature has no database or in-memory data structures. The "entities" are files on the
developer's filesystem. This document defines their structure, constraints, and relationships.

---

## File Entities

### Entity: `scripts/setup-mac.sh`

The executable setup script.

| Attribute | Value |
|---|---|
| Path | `scripts/setup-mac.sh` (relative to project root) |
| Type | Executable Bash script |
| Permissions | `755` (owner read/write/execute; group and other read/execute) |
| Arguments | None required; no flags defined |
| Side effects | Reads `.env.example`; writes `.env`; conditionally appends to `.gitignore` |
| Idempotency | Safe to run multiple times — all writes are guarded by existence checks |

**Lifecycle**:
```
Created once in repository → committed to source control → run by developer on first clone
```

---

### Entity: `.env.example`

The template configuration file committed to the repository.

| Attribute | Constraint |
|---|---|
| Path | `.env.example` (project root) |
| Format | `KEY=` (one variable per line, values strictly empty) |
| Committed | Yes — always present in source control |
| Secret values | PROHIBITED — script exits non-zero if any `KEY=value` with non-empty value |
| Comments | Permitted — lines starting with `#` are ignored by the script |
| Blank lines | Permitted — ignored by the script |

**Valid line formats**:
```
# This is a comment
DATABASE_URL=
OPENAI_API_KEY=
LANGFUSE_SECRET_KEY=
```

**Invalid line formats** (script rejects):
```
DATABASE_URL=postgres://user:pass@localhost/db   # non-empty value
OPENAI_API_KEY=sk-abc123                          # non-empty value
```

**Validation rule**: `grep -vE '^\s*#|^\s*$' .env.example | grep -qE '^[A-Za-z_][A-Za-z0-9_]*=.+'`

---

### Entity: `.env`

The developer-local configuration file created by the script.

| Attribute | Constraint |
|---|---|
| Path | `.env` (project root) |
| Format | `KEY=value` (developer fills in real values after creation) |
| Committed | PROHIBITED — must be listed in `.gitignore` |
| Creation | Copied from `.env.example` by the script on first run |
| Overwrite | PROHIBITED — script detects existence and skips creation |
| Initial state | Identical to `.env.example` (all empty values) immediately after creation |

**State transitions**:
```
[absent] ──(script first run)──→ [created, empty values]
                                          │
                                          ↓ (developer fills in)
                                  [populated with real values]
[present] ──(script re-run)──→ [unchanged, skip message printed]
```

---

### Entity: `.gitignore`

The Git exclusion file at the project root.

| Attribute | Constraint |
|---|---|
| Path | `.gitignore` (project root) |
| Required entry | `.env` (exact line, checked by script before `.env` creation) |
| Script behaviour | If `.env` entry absent: appends `.env` line; if file missing: creates it |
| Other entries | Not managed by this script; pre-existing content preserved |

**Guard check**: `grep -qxF '.env' .gitignore 2>/dev/null`

---

## Entity Relationships

```
.env.example ──(validated, then cp)──→ .env
     │                                   │
     │ committed to repo                 │ gitignored (enforced by script)
     ↓                                   ↓
 source control                    developer workstation only

setup-mac.sh ──reads──→ .env.example
             ──writes──→ .env (if absent)
             ──appends──→ .gitignore (if .env entry missing)
```

---

## Invariants

1. `.env` MUST exist in `.gitignore` before `.env` is created.
2. `.env.example` MUST contain only empty values when committed.
3. `.env` is NEVER overwritten by `setup-mac.sh` regardless of its current content.
4. `setup-mac.sh` NEVER creates `.env` if any prerequisite check fails (OrbStack absent,
   OrbStack stopped, `.env.example` missing, secrets in `.env.example`).
5. `setup-mac.sh` DOES create `.env` even when free memory is below 3000 MB (warning only).
