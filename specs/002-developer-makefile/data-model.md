# Data Model: Developer Makefile

**Feature**: `002-developer-makefile`
**Date**: 2026-05-28

---

## Overview

This feature has no database or in-memory data structures. The "entities" are files on the
developer's filesystem. This document defines their structure, constraints, and relationships.

---

## File Entities

### Entity: `Makefile`

The primary deliverable — a GNU Make build file at the project root.

| Attribute | Value |
|---|---|
| Path | `Makefile` (project root) |
| Type | GNU Make build file |
| Permissions | `644` (readable by all; no execute bit needed — `make` reads it directly) |
| Arguments | Variable `svc=<name>` accepted by `restart` and `logs` targets only |
| Side effects | Invokes Docker Compose and scripts in `scripts/`; never writes files itself |
| Idempotency | All targets safe to invoke repeatedly except `down-v` (destructive by design) |

**Key variables defined inside**:

| Variable | Value | Purpose |
|---|---|---|
| `COMPOSE` | `docker compose --env-file .env` | Used by every Compose invocation |
| `.DEFAULT_GOAL` | `help` | Bare `make` shows help |

**Targets**:

| Target | Accepts `svc=` | Exits non-zero if omitted | Notes |
|---|---|---|---|
| `help` | No | N/A | Default goal; lists all targets |
| `up-core` | No | N/A | Starts `core` profile |
| `up-obs` | No | N/A | Starts `obs` profile |
| `up-auth` | No | N/A | Starts `auth` profile |
| `up-safety` | No | N/A | Starts `safety` profile |
| `up-gov` | No | N/A | Starts `gov` profile |
| `up-portal` | No | N/A | Starts `portal` profile |
| `up-all` | No | N/A | Starts all six profiles |
| `down` | No | N/A | Stops all containers; preserves volumes |
| `down-v` | No | N/A | Stops containers and removes volumes |
| `restart` | Yes — required | Yes | Restarts named service only |
| `logs` | Yes — required | Yes | Tails named service logs |
| `ps` | No | N/A | Lists containers with ports and status |
| `stats` | No | N/A | Live per-container resource metrics |
| `smoke` | No | N/A | Runs smoke-test.sh; exits 1 on failure |
| `seed-kong` | No | N/A | Applies Kong config (idempotent) |
| `seed-vault` | No | N/A | Writes Vault secrets (idempotent) |

**Lifecycle**:
```
Created once in repository → committed to source control → used by every developer on every interaction
```

---

### Entity: `.env`

The developer-local configuration file loaded by every Compose invocation via `--env-file`.

| Attribute | Constraint |
|---|---|
| Path | `.env` (project root) |
| Format | `KEY=value` (developer fills in real credentials) |
| Committed | PROHIBITED — must be listed in `.gitignore` |
| Read by | `$(COMPOSE)` via `--env-file .env` on every invocation |
| Created by | `scripts/setup-mac.sh` (feature 001) |
| Written by | Never written by the Makefile |

---

### Entity: `docker-compose.yml`

The Compose file that defines all services and profiles.

| Attribute | Constraint |
|---|---|
| Path | `docker-compose.yml` (project root) |
| Profiles defined | `core`, `obs`, `auth`, `safety`, `gov`, `portal` |
| Read by | `$(COMPOSE)` on every `up-*`, `down`, `down-v`, `restart`, `logs`, `ps` invocation |
| Written by | Never written by the Makefile |

---

### Entity: `scripts/seed-kong.sh`

The script that applies Kong route and service configuration via the Kong Admin API.

| Attribute | Constraint |
|---|---|
| Path | `scripts/seed-kong.sh` (project root) |
| Idempotency | REQUIRED — MUST use `PUT` (upsert) semantics, never bare `POST` without name field |
| Invoked by | `make seed-kong` |
| Exit code | 0 on success; non-zero on failure (propagated to make) |
| Pre-requisite | Kong (`core` profile) must be running and healthy |

---

### Entity: `scripts/seed-vault.sh`

The script that bootstraps Vault with the platform's required secrets.

| Attribute | Constraint |
|---|---|
| Path | `scripts/seed-vault.sh` (project root) |
| Idempotency | REQUIRED — `vault kv put` overwrites by path; inherently safe to re-run |
| Invoked by | `make seed-vault` |
| Exit code | 0 on success; non-zero on failure (propagated to make) |
| Pre-requisite | Vault (`auth` profile) must be running and unsealed |

---

### Entity: `scripts/smoke-test.sh`

The end-to-end health probe script.

| Attribute | Constraint |
|---|---|
| Path | `scripts/smoke-test.sh` (project root) |
| Invoked by | `make smoke` |
| Exit code | 0 if all probes pass; non-zero if any probe fails |
| Pre-requisite | Platform running and seeded; Kong accepting traffic on :8080 |

---

## Entity Relationships

```
Makefile
  ├─ reads (via COMPOSE) ──→ .env
  ├─ reads (via COMPOSE) ──→ docker-compose.yml
  ├─ invokes ──────────────→ scripts/seed-kong.sh  (make seed-kong)
  ├─ invokes ──────────────→ scripts/seed-vault.sh (make seed-vault)
  └─ invokes ──────────────→ scripts/smoke-test.sh (make smoke)

.env ──────────────────────→ committed: NO (gitignored)
.env.example ──────────────→ committed: YES (names only, no values)
Makefile ──────────────────→ committed: YES
scripts/*.sh ──────────────→ committed: YES
```

---

## Invariants

1. `COMPOSE` MUST be defined as `docker compose --env-file .env` and used in every recipe
   that invokes Docker Compose.
2. `restart` and `logs` MUST exit non-zero and touch no container when `svc=` is absent.
3. `seed-kong` and `seed-vault` MUST be safe to run multiple times without duplicating config.
4. `down` MUST preserve named Docker volumes; `down-v` MUST remove them.
5. Every target MUST appear in `make help` output via a `##` comment.
6. The Makefile MUST NOT embed any credential values — all secrets come from `.env`.
7. All targets MUST be declared `.PHONY`.
