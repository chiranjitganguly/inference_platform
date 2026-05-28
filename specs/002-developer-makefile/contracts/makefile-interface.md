# Contract: Makefile Target Interface

**Feature**: `002-developer-makefile`
**Date**: 2026-05-28

---

## Overview

This document defines the complete public interface of the `Makefile`: every target, its
accepted inputs, exit codes, stdout/stderr behaviour, and side effects. Downstream targets
(e.g., CI/CD invoking `make smoke`) rely on this contract.

---

## Global Invariants

| Invariant | Rule |
|---|---|
| `COMPOSE` variable | Defined as `docker compose --env-file .env`; used by every Compose recipe |
| `.PHONY` | All targets declared `.PHONY`; no target name collides with a filename |
| Default goal | `make` with no target is equivalent to `make help` |
| Secret handling | No credential values in the Makefile; all secrets come from `.env` |

---

## Target Contracts

### `help`

| Field | Value |
|---|---|
| Invocation | `make help` or `make` |
| Arguments | None |
| Exit code | `0` always |
| Stdout | Formatted list of all targets with one-line descriptions |
| Stderr | None |
| Side effects | None |

**Output format**:
```
Usage:
  make <target>

Targets:
  help                 Show available targets
  up-core              Start core profile (LiteLLM, Kong, Redis, Postgres, Prometheus, Grafana)
  ...
```

---

### `up-core` / `up-obs` / `up-auth` / `up-safety` / `up-gov` / `up-portal`

| Field | Value |
|---|---|
| Invocation | `make up-<group>` |
| Arguments | None |
| Exit code | `0` on success; Compose exit code (non-zero) on failure |
| Stdout | Docker Compose output (container start log) |
| Stderr | Docker Compose errors on failure |
| Side effects | Starts containers in the named profile in detached mode (`-d`) |
| Idempotency | Safe — already-running containers are left unchanged |

**Profile → services mapping** (informational):

| Target | Profile | Key services |
|---|---|---|
| `up-core` | `core` | LiteLLM, Kong, Redis×2, Postgres, Prometheus, Grafana |
| `up-obs` | `obs` | Loki, OTel Collector, Alertmanager, Jaeger, Phoenix Arize, Langfuse |
| `up-auth` | `auth` | Keycloak, OPA, Vault |
| `up-safety` | `safety` | Presidio analyzer, Presidio anonymizer, LLM Guard, Guardrails |
| `up-gov` | `gov` | MLflow |
| `up-portal` | `portal` | Swagger UI, Portal backend, Platform UI |

---

### `up-all`

| Field | Value |
|---|---|
| Invocation | `make up-all` |
| Arguments | None |
| Exit code | `0` on success; Compose exit code on failure |
| Stdout | Docker Compose output for all six profiles |
| Side effects | Starts all containers from all six profiles in detached mode |
| Idempotency | Safe — already-running containers left unchanged |

---

### `down`

| Field | Value |
|---|---|
| Invocation | `make down` |
| Arguments | None |
| Exit code | `0` always (including when no containers are running) |
| Stdout | Docker Compose output |
| Side effects | Stops and removes all containers; **named volumes are preserved** |
| Idempotency | Safe — running when no containers exist exits `0` |

---

### `down-v`

| Field | Value |
|---|---|
| Invocation | `make down-v` |
| Arguments | None |
| Exit code | `0` on success; non-zero on failure |
| Stdout | Docker Compose output |
| Side effects | Stops and removes all containers **and removes all named Docker volumes** |
| Idempotency | Safe to re-run (nothing to delete if already torn down) |
| Warning | Destructive — all persistent data (databases, Vault state) is erased |

---

### `restart`

| Field | Value |
|---|---|
| Invocation | `make restart svc=<service-name>` |
| Arguments | `svc=<name>` — **required** |
| Exit code (success) | `0` |
| Exit code (missing `svc=`) | `2` (Make error exit) |
| Stdout (success) | Docker Compose restart output for the named service |
| Stderr (missing `svc=`) | `Makefile:<line>: *** Usage: make restart svc=<service-name>. Stop.` |
| Side effects | Restarts exactly one container; all others unchanged |
| Idempotency | Safe |

**Error output example** (missing `svc=`):
```
Makefile:42: *** Usage: make restart svc=<service-name>.  Stop.
```

---

### `logs`

| Field | Value |
|---|---|
| Invocation | `make logs svc=<service-name>` |
| Arguments | `svc=<name>` — **required** |
| Exit code (success) | Does not exit until interrupted (Ctrl-C) |
| Exit code (missing `svc=`) | `2` (Make error exit) |
| Stdout (success) | Live streaming log output for the named container |
| Stderr (missing `svc=`) | `Makefile:<line>: *** Usage: make logs svc=<service-name>. Stop.` |
| Side effects | None (read-only) |

---

### `ps`

| Field | Value |
|---|---|
| Invocation | `make ps` |
| Arguments | None |
| Exit code | `0` on success |
| Stdout | Table of Compose-managed containers: name, image, status, ports |
| Side effects | None (read-only) |

---

### `stats`

| Field | Value |
|---|---|
| Invocation | `make stats` |
| Arguments | None |
| Exit code | Does not exit until interrupted (Ctrl-C) |
| Stdout | Live streaming per-container CPU and memory metrics |
| Side effects | None (read-only) |

---

### `smoke`

| Field | Value |
|---|---|
| Invocation | `make smoke` |
| Arguments | None |
| Exit code | `0` if all probes pass; non-zero if any probe fails |
| Stdout | Probe results (pass/fail per check) |
| Side effects | HTTP requests to platform endpoints on `:8080` |
| Precondition | Platform running and seeded; Kong healthy |

---

### `seed-kong`

| Field | Value |
|---|---|
| Invocation | `make seed-kong` |
| Arguments | None |
| Exit code | `0` on success; non-zero on failure |
| Stdout | Output from `scripts/seed-kong.sh` |
| Side effects | Creates or updates Kong routes and services via Admin API (:8001) |
| Idempotency | **Yes** — `PUT` upsert semantics; safe to re-run |
| Precondition | `core` profile running; Kong healthy on `:8001` |

---

### `seed-vault`

| Field | Value |
|---|---|
| Invocation | `make seed-vault` |
| Arguments | None |
| Exit code | `0` on success; non-zero on failure |
| Stdout | Output from `scripts/seed-vault.sh` |
| Side effects | Writes secrets to Vault KV store |
| Idempotency | **Yes** — `vault kv put` overwrites; safe to re-run |
| Precondition | `auth` profile running; Vault running and unsealed |
