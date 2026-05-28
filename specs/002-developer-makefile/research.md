# Research: Developer Makefile

**Feature**: `002-developer-makefile`
**Date**: 2026-05-28
**Status**: Complete — all decisions resolved

---

## Decision 1: Self-Documenting Help Target

**Decision**: Use the `##` double-hash comment convention with an `awk` one-liner to generate
`make help` output. Set `.DEFAULT_GOAL := help` so that bare `make` shows help.

**Rationale**:
- Every target carries its own documentation in the source — no separate list to maintain.
- The `awk` pattern matches lines of the form `target: ## description` and formats them into
  aligned columns with ANSI colour. It is a well-established GNU Make idiom with zero
  external dependencies.
- `.DEFAULT_GOAL := help` satisfies FR-014 and SC-006 — `make` with no arguments shows help.

**Implementation pattern**:
```makefile
.DEFAULT_GOAL := help

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} \
	/^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
```

**Alternatives considered**:
- Separate `HELP.md` file: Diverges from the Makefile immediately on the first edit. Rejected.
- `make -p` output: Verbose and unformatted — not suitable for developer UX. Rejected.
- Single-hash comment: Standard Make comments are stripped from `$?` help listings; only
  `##` is conventionally used to distinguish doc comments from implementation comments.
  Rejected.

---

## Decision 2: COMPOSE Variable

**Decision**: Define `COMPOSE := docker compose --env-file .env` once at the top of the
Makefile and use `$(COMPOSE)` in every recipe that invokes Docker Compose.

**Rationale**:
- A single definition is the only place that ever changes if the env file path or Compose
  flags need updating. Every recipe inherits the change automatically.
- `--env-file .env` ensures the project secrets file is loaded unconditionally, satisfying
  FR-013 and FR-016. Without this flag, Docker Compose looks for `.env` by convention but
  the flag makes the dependency explicit and portable across working directory variations.
- `:=` (immediate assignment) is preferred over `=` (deferred) for a simple string — it
  avoids repeated re-evaluation and is idiomatic for variable declarations in GNU Make.

**Implementation pattern**:
```makefile
COMPOSE := docker compose --env-file .env
```

**Alternatives considered**:
- Inline flag per recipe: Duplication risk — one recipe missing `--env-file` silently drops
  secrets. Rejected.
- `docker-compose` (legacy V1 binary): Deprecated; OrbStack ships the V2 plugin. Rejected.
- `DOCKER_COMPOSE` as variable name: Less idiomatic for this codebase. `COMPOSE` is concise
  and matches the clarification direction from spec session. Accepted.

---

## Decision 3: Required `svc=` Guard for `restart` and `logs`

**Decision**: Use `ifndef svc` / `$(error ...)` to block `make restart` and `make logs` when
`svc=` is not provided. The error message includes the correct invocation form.

**Rationale**:
- `ifndef svc` is evaluated at parse time before the recipe shell is invoked — the error
  fires immediately with a clear message, no container touched.
- `$(error ...)` exits Make with a non-zero status (exit 2), satisfying SC-002 and SC-003.
- The error message is printed to stderr by Make, consistent with the spec requirement.

**Implementation pattern**:
```makefile
restart: ## Restart a service: make restart svc=<name>
ifndef svc
	$(error Usage: make restart svc=<service-name>)
endif
	$(COMPOSE) restart $(svc)

logs: ## Tail logs for a service: make logs svc=<name>
ifndef svc
	$(error Usage: make logs svc=<service-name>)
endif
	$(COMPOSE) logs -f $(svc)
```

**Alternatives considered**:
- `@if [ -z "$(svc)" ]; then echo "..."; exit 1; fi`: Shell-level guard runs after Make
  starts recipe expansion; `$(error)` is cleaner and fires earlier. Rejected.
- Defaulting `svc` to a value (e.g., all services): Explicitly prohibited by FR-005 and
  FR-007 — the constraint is intentional safety, not convenience. Rejected.

---

## Decision 4: Profile Flag Syntax for Startup Targets

**Decision**: Pass `--profile <name>` to the `$(COMPOSE) up -d` invocation per target. For
`up-all`, chain all six `--profile` flags in a single invocation.

**Rationale**:
- `--profile` is the Docker Compose V2 canonical flag for named profile activation. It is
  stable across OrbStack's bundled Compose versions.
- A single `$(COMPOSE) --profile core --profile obs … up -d` in `up-all` starts all services
  in one Compose process — correct ordering, shared network, single operation.
- Each `up-<group>` target using a single `--profile` means profiles are independently
  additive: running `up-core` then `up-obs` activates both profiles against the same
  `docker-compose.yml`, leaving existing containers running.

**Implementation pattern**:
```makefile
up-core: ## Start core profile (LiteLLM, Kong, Redis, Postgres, Prometheus, Grafana)
	$(COMPOSE) --profile core up -d

up-all: ## Start all profiles
	$(COMPOSE) --profile core --profile obs --profile auth \
	           --profile safety --profile gov --profile portal up -d
```

**Alternatives considered**:
- `COMPOSE_PROFILES` environment variable: Requires exporting before invocation; harder to
  audit per-target. Per-flag approach is explicit and self-contained. Rejected.
- Separate `docker-compose.<profile>.yml` files: Does not match the project's existing
  single `docker-compose.yml` with profiles. Rejected.

---

## Decision 5: Idempotent Seed Targets

**Decision**: Delegate idempotency to the seed scripts (`scripts/seed-kong.sh`,
`scripts/seed-vault.sh`). The Makefile targets simply invoke the scripts without flags.
The scripts themselves MUST use upsert semantics (Kong Admin API `PUT`, Vault `kv put`).

**Rationale**:
- The Makefile is not the right layer for idempotency logic — the scripts own it.
- Kong Admin API `PUT /services/{name}` and `PUT /routes/{name}` are upsert operations —
  they create-or-update, never duplicate, when a stable `name` field is used.
- `vault kv put` overwrites the secret at a path — inherently idempotent (last write wins,
  no duplication possible in the KV engine).
- The Makefile target therefore has zero extra logic: `$(BASH) scripts/seed-kong.sh`.

**Implementation pattern**:
```makefile
seed-kong: ## Seed Kong routes and services (idempotent)
	bash scripts/seed-kong.sh

seed-vault: ## Seed Vault secrets (idempotent)
	bash scripts/seed-vault.sh
```

**Seed script idempotency requirements** (enforced at script level, not Makefile level):
- `seed-kong.sh`: Use `PUT /services/<name>` and `PUT /routes/<name>` — never `POST`
  without a name field.
- `seed-vault.sh`: Use `vault kv put <path> key=value` — overwrite is the correct semantic.

---

## Decision 6: `down` vs `down-v` Targets

**Decision**: Provide two teardown targets: `down` (stop and remove containers, preserve
volumes) and `down-v` (stop containers and remove volumes). Both are `.PHONY`.

**Rationale**:
- `down` is the safe everyday teardown — persistent data (PostgreSQL, Keycloak, MLflow) in
  named volumes survives for the next `up-*` without re-seeding.
- `down-v` is the nuclear option for a full environment reset — required when database
  schema migrations need a clean slate or when secrets in Vault need to be fully re-seeded.
- Having `down-v` as an explicit separate target prevents accidental volume deletion during
  routine development; the developer must consciously type the longer command.

**Implementation pattern**:
```makefile
down: ## Stop all containers (preserves volumes)
	$(COMPOSE) down

down-v: ## Stop all containers and remove volumes (destructive)
	$(COMPOSE) down -v
```

---

## Decision 7: `.PHONY` Declarations

**Decision**: Declare every target in a single `.PHONY` line at the top of the Makefile.

**Rationale**:
- Every target in this Makefile is an action, not a file. Without `.PHONY`, Make skips the
  recipe if a file with the same name exists (e.g., a file named `logs` would silently
  prevent `make logs` from running).
- A single combined `.PHONY` declaration is cleaner than per-target declarations and is
  idiomatic for action-only Makefiles.

**Implementation pattern**:
```makefile
.PHONY: help up-core up-obs up-auth up-safety up-gov up-portal up-all \
        down down-v restart logs ps stats smoke seed-kong seed-vault
```

---

## Decision 8: `ps` and `stats` Implementation

**Decision**: `ps` delegates to `$(COMPOSE) ps`; `stats` delegates to `docker stats` (not
`$(COMPOSE) stats` — Compose wraps docker stats but with less useful output).

**Rationale**:
- `$(COMPOSE) ps` shows all services defined in `docker-compose.yml` with their port
  mappings and status — exactly what a developer needs to see.
- `docker stats` (bare Docker command) provides live streaming per-container CPU and memory
  — more granular than `$(COMPOSE) stats` which filters to Compose-managed containers only
  but may omit useful columns on some OrbStack versions.
- `docker stats` is a separate binary invocation, not via `$(COMPOSE)`, because it is a
  direct container runtime query, not a Compose lifecycle operation.

**Implementation pattern**:
```makefile
ps: ## Show running containers
	$(COMPOSE) ps

stats: ## Live resource usage per container
	docker stats
```
