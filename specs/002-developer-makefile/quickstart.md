# Quickstart: Developer Makefile

**Feature**: `002-developer-makefile`
**Updated**: 2026-05-28

---

## Prerequisites

| Requirement | How to check | Fix if missing |
|---|---|---|
| macOS 14.0+ | `sw_vers -productVersion` | Upgrade macOS |
| OrbStack running | `orbctl status` | Open OrbStack.app |
| `.env` populated | `cat .env` | Run `bash scripts/setup-mac.sh` |
| GNU Make | `make --version` | `xcode-select --install` |
| Project root | `ls Makefile` | `cd` into the repo root |

---

## First Run (new developer)

After running `scripts/setup-mac.sh` and filling in `.env`:

```bash
make up-core       # 1. Start core services
make seed-kong     # 2. Bootstrap Kong routes
make seed-vault    # 3. Bootstrap Vault secrets
make smoke         # 4. Verify everything is healthy
```

---

## Starting the Platform

### Start a single profile

```bash
make up-core      # LiteLLM + Kong + Redis×2 + Postgres + Prometheus + Grafana
make up-obs       # Loki + OTel + Alertmanager + Jaeger + Phoenix Arize + Langfuse
make up-auth      # Keycloak + OPA + Vault
make up-safety    # Presidio + LLM Guard + Guardrails service
make up-gov       # MLflow
make up-portal    # Swagger UI + Portal backend + Platform UI
```

### Start all profiles

```bash
make up-all       # Starts all six profiles together
```

### Combine profiles

Profiles are additive — you can start them in any order and all remain running:

```bash
make up-core
make up-obs       # Both core and obs are now running
make up-auth      # core, obs, and auth are now running
```

### Stop everything

```bash
make down         # Stop containers — volumes (databases) are preserved
make down-v       # Stop containers AND remove all volumes (full reset)
```

> **Warning**: `make down-v` erases all persistent data (databases, Vault state). You will
> need to re-run `make seed-kong` and `make seed-vault` after `make up-*`.

---

## Daily Development Loop

### Restart a service after a config change

```bash
make restart svc=litellm    # Restart LiteLLM after editing services/litellm/config.yaml
make restart svc=kong        # Restart Kong after changing routes
make restart svc=guardrails  # Restart Guardrails after updating main.py
```

### Tail logs

```bash
make logs svc=kong        # Watch Kong access logs
make logs svc=litellm     # Watch LiteLLM model routing logs
make logs svc=guardrails  # Watch Guardrails pipeline logs
```

Press `Ctrl-C` to stop tailing.

### Check platform state

```bash
make ps      # List all containers with ports and status
make stats   # Live CPU/memory per container (Ctrl-C to exit)
```

---

## Verification

### Run smoke tests

```bash
make smoke   # All checks pass → exits 0; any failure → exits non-zero with detail
```

Run `make smoke` after every change before opening a PR.

---

## Seeding (first run or after `make down-v`)

```bash
make seed-kong    # Configure Kong routes and services (safe to re-run)
make seed-vault   # Write secrets to Vault (safe to re-run)
```

Both targets are idempotent — re-running them on an already-seeded service updates
configuration without creating duplicates.

---

## Getting Help

```bash
make          # Show all available targets (same as make help)
make help     # Same as above
```

---

## Troubleshooting

### `make restart` prints a usage error

You must always specify the service name:
```bash
make restart svc=<service-name>
```
Omitting `svc=` is intentional — to prevent accidentally restarting the entire platform.

### `make logs` prints a usage error

Same pattern — always supply `svc=`:
```bash
make logs svc=<service-name>
```

### `make up-core` fails with "Cannot connect to Docker daemon"

OrbStack is not running. Open OrbStack.app from your Applications folder, wait for the menu
bar icon to appear, then retry.

### `make smoke` fails

A service is unhealthy. Check which probe failed in the output, then:
```bash
make logs svc=<failing-service>   # Inspect logs
make restart svc=<failing-service> # Attempt restart
make smoke                         # Re-verify
```

### `make seed-kong` fails with connection error

Kong is not ready. Verify it is running:
```bash
make ps   # Check kong is Up
```
If absent, run `make up-core` first.

### Container starts but crashes immediately

Credentials in `.env` are missing or wrong. Check the service logs:
```bash
make logs svc=<service>
```
Then fill in the missing values in `.env` and restart:
```bash
make restart svc=<service>
```

---

## Re-running Make

All `up-*` targets are safe to re-run — already-running containers are left unchanged.
All `seed-*` targets are safe to re-run — existing configuration is updated, not duplicated.
