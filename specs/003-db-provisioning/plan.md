# Implementation Plan: Automated Database Provisioning

**Branch**: `003-db-provisioning` | **Date**: 2026-05-28 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/003-db-provisioning/spec.md`

---

## Summary

Create `scripts/init-db.sql` — a SQL script that creates the six application databases
(`litellm`, `keycloak`, `mlflow`, `kong`, `phoenix`, `langfuse`) and grants all privileges
to the platform user — and wire it into the `postgres:16-alpine` service in `docker-compose.yml`
via the `/docker-entrypoint-initdb.d/` mount. PostgreSQL's built-in init mechanism guarantees
the script runs only on a fresh `pg_data` volume, making restarts automatically idempotent.

---

## Technical Context

**Language/Version**: SQL (PostgreSQL 16 dialect); Docker Compose v2 YAML

**Primary Dependencies**:
- `postgres:16-alpine` — locked image per constitution §3
- Docker Compose named volume (`pg_data`) for data persistence

**Storage**: Docker named volume `pg_data` mounted at `/var/lib/postgresql/data`

**Testing**: `psql` via `docker exec` against the running container; `make smoke`

**Target Platform**: Local Mac with OrbStack + Docker Compose (`core` profile); identical
mechanism works in any environment that honours `/docker-entrypoint-initdb.d/`

**Project Type**: Infrastructure init script + Docker Compose service definition

**Performance Goals**:
- Fresh-volume provisioning completes within 30 s of postgres reaching healthy (SC-001)
- Idempotent skip on restart adds < 2 s to startup time (SC-002)

**Constraints**:
- No prompt content handled — this feature is data-plane infrastructure only
- Script must not error on a data volume that already contains a PostgreSQL cluster
- `postgres:16-alpine` image tag must not change without an ADR (constitution §3)
- `pg_data` is the canonical volume name; other services that depend on postgres must
  reference this same volume name in future specs

**Scale/Scope**: 6 databases, 1 platform user, 1 SQL file, 1 docker-compose.yml service block

---

## Constitution Check

*GATE: Must pass before implementation. Re-checked after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I — Request Flow Integrity | ✅ Not applicable | Database provisioning is infrastructure; not in the Kong → Guardrails → LiteLLM chain |
| II — Prompt Content is Ephemeral | ✅ Not applicable | No prompt content handled at this layer |
| III — OpenAI API Compatibility | ✅ Not applicable | No inference endpoint created |
| IV — Defence in Depth | ✅ Not applicable | No enforcement layer affected |
| V — Falsifiable Acceptance Criteria | ✅ Required and met | Acceptance criteria use `docker exec psql` commands — see research.md §Acceptance Criteria |

**Additional constitution checks:**

| Rule | Status | Notes |
|---|---|---|
| Locked image `postgres:16-alpine` | ✅ Confirmed | User input and constitution §3 agree |
| Healthcheck on every service | ✅ Required | `pg_isready` healthcheck defined in docker-compose.yml |
| No secrets in docker-compose.yml | ✅ Required | All values via `${VAR}` references to `.env` |
| `down-v` destroys pg_data | ✅ Expected | Makefile already has `down-v` target; operators are warned in quickstart |
| Memory budget | ✅ Within limits | Adding 6 empty databases to postgres has negligible memory impact; core profile stays within ~620 MB |

**Gate result: PASS — no violations.**

---

## Project Structure

### Documentation (this feature)

```text
specs/003-db-provisioning/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
scripts/
└── init-db.sql          # NEW — SQL init script: 6x conditional CREATE DATABASE + GRANT

docker-compose.yml       # NEW — postgres service (core profile) with pg_data volume,
                         #       init-db.sql bind-mount, healthcheck, env refs

.env.example             # EXISTING — already declares POSTGRES_USER, POSTGRES_PASSWORD,
                         #            POSTGRES_DB — no changes needed
Makefile                 # EXISTING — no changes needed
```

**Structure Decision**: Flat single-project layout. The deliverable is two files: one SQL
script and one Docker Compose service definition. No application source tree needed.

---

## Phase 0: Research

### Decision 1 — Idempotency Mechanism

**Decision**: Rely on PostgreSQL's built-in init directory semantics for restart idempotency;
add per-database existence guards for partial-init recovery.

**Rationale**: The `postgres:16-alpine` image executes every file under
`/docker-entrypoint-initdb.d/` once, on first start only, when `$PGDATA` is empty. On every
subsequent start the directory is ignored entirely. This satisfies FR-004 (idempotency) and
SC-002 (< 2 s overhead on restart) without any application-level logic.

For the edge case where the container is killed mid-init (container OOM, power loss after 3
of 6 databases created), the data directory is partially initialised and not empty, so the
init scripts are skipped on the next start. To handle this without leaving the platform in
a broken state, each `CREATE DATABASE` statement uses an existence guard so it is safe to
run repeatedly even if init scripts are somehow re-executed.

**Alternatives considered**:
- Sidecar container that polls postgres and runs provisioning — rejected; adds complexity,
  a second container, and a race condition that the init directory approach avoids
- Application-level retry logic — rejected; constitution Principle I prohibits bypassing
  startup ordering guarantees; FR-007 requires infrastructure-level enforcement

### Decision 2 — Conditional CREATE DATABASE Syntax

**Decision**: Use the `\gexec` meta-command pattern for conditional database creation.

**Rationale**: PostgreSQL 16 does not support `CREATE DATABASE IF NOT EXISTS`. The standard
idiomatic pattern for conditional database creation in a `.sql` init file is:

```sql
SELECT format('CREATE DATABASE %I', 'litellm')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'litellm')\gexec
```

`\gexec` executes the result of the preceding query as SQL. If the database exists, the
SELECT returns no rows and `\gexec` executes nothing. If the database is absent, it executes
`CREATE DATABASE litellm`. This is fully idempotent.

**Alternatives considered**:
- `DO $$ ... PERFORM dblink_exec(...) $$` — rejected; requires `dblink` extension, which
  is not installed by default in `postgres:16-alpine`
- `.sh` shell script in `/docker-entrypoint-initdb.d/` — viable but rejected because the
  user explicitly specified a `.sql` file; a shell script would also need to handle
  `|| true` suppression of errors which obscures real failures

### Decision 3 — GRANT Privilege Target

**Decision**: Use `CURRENT_USER` as the grantee in all `GRANT ALL PRIVILEGES` statements.

**Rationale**: The `.sql` init file is executed by psql as `$POSTGRES_USER`. Using
`CURRENT_USER` avoids hardcoding the username in the script, keeping it portable across
environments with different `POSTGRES_USER` values. PostgreSQL 16 accepts `CURRENT_USER`
as a valid role name in `GRANT` syntax. Since `POSTGRES_USER` is the database owner, the
grant is logically redundant (owner already has all privileges) but the statement is
explicitly included so:
1. The intent is self-documenting
2. If a future operator separates the superuser from the platform user, the grant statement
   remains meaningful without script changes

**Alternatives considered**:
- Hardcoding `platform` as the username — rejected; couples the script to a specific
  environment variable value
- Omitting the GRANT entirely — rejected; FR-003 requires an explicit grant statement
  and SC-003 requires verified privilege assignment

### Decision 4 — Volume and Mount Strategy

**Decision**: Docker named volume `pg_data` for data persistence; read-only bind-mount for
the init script.

**Rationale**:
- Named volume `pg_data` (not a host path mount) ensures postgres data is managed by
  Docker and survives container recreation while being cleanly removable via `make down-v`
- Init script mounted as `./scripts/init-db.sql:/docker-entrypoint-initdb.d/init.sql:ro`
  — the `:ro` flag prevents the container from modifying the source script
- Naming the mounted file `init.sql` (not `init-db.sql`) avoids a directory traversal
  ambiguity; PostgreSQL executes files alphabetically so a single file needs no ordering prefix

**Alternatives considered**:
- Host-path bind mount for data — rejected; loses Docker volume lifecycle management,
  creates permission issues on some Mac filesystems
- Baking the SQL into a custom Docker image — rejected; adds an unnecessary build step
  and a custom image to maintain for a file that changes rarely

### Decision 5 — Healthcheck

**Decision**: `pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}` as the postgres healthcheck.

**Rationale**: Constitution §3 requires healthchecks on every service. `pg_isready` is
available in the `postgres:16-alpine` image with no additional dependencies. It returns
exit 0 only after the server is accepting connections. Combined with Docker Compose
`depends_on: postgres: condition: service_healthy`, downstream services are blocked until
postgres is ready — satisfying FR-007 and the startup ordering clarification.

### Acceptance Criteria (Falsifiable — Constitution Principle V)

```bash
# AC-1: All 6 databases present after fresh start
docker exec $(docker ps -qf name=postgres) \
  psql -U "$POSTGRES_USER" -lqt | \
  awk '{print $1}' | \
  grep -E '^(litellm|keycloak|mlflow|kong|phoenix|langfuse)$' | \
  wc -l | grep -q 6 && echo PASS || echo FAIL

# AC-2: Platform user has ALL PRIVILEGES on each database
for db in litellm keycloak mlflow kong phoenix langfuse; do
  docker exec $(docker ps -qf name=postgres) \
    psql -U "$POSTGRES_USER" -d "$db" -c "CREATE TABLE IF NOT EXISTS _probe (id int); DROP TABLE _probe;" \
    && echo "$db: PASS" || echo "$db: FAIL"
done

# AC-3: Idempotent restart — no errors on second start
make down && make up-core
docker exec $(docker ps -qf name=postgres) \
  psql -U "$POSTGRES_USER" -lqt | \
  awk '{print $1}' | \
  grep -E '^(litellm|keycloak|mlflow|kong|phoenix|langfuse)$' | \
  wc -l | grep -q 6 && echo PASS || echo FAIL

# AC-4: postgres service reports healthy
docker inspect $(docker ps -qf name=postgres) \
  --format '{{.State.Health.Status}}' | grep -q healthy && echo PASS || echo FAIL

# AC-5: Memory budget — core profile stays within ~620 MB
docker stats --no-stream --format "{{.MemUsage}}" $(docker ps -qf name=postgres)
# Verify postgres is not the outlier blowing the budget
```

---

## Phase 1: Design

### data-model.md — Logical Database Layout

Each database is an independent logical namespace within the shared PostgreSQL 16 instance.
This script establishes the databases only; schema creation (tables, indexes, extensions) is
performed by each service's ORM or migration tool on first connection.

| Database | Owner | Used by Service | Phase introduced |
|---|---|---|---|
| `litellm` | POSTGRES_USER | LiteLLM Proxy | Phase 01 (core) |
| `keycloak` | POSTGRES_USER | Keycloak SSO | Phase 04 (auth) |
| `mlflow` | POSTGRES_USER | MLflow Model Registry | Phase 05 (gov) |
| `kong` | POSTGRES_USER | Kong API Gateway | Phase 01 (core) |
| `phoenix` | POSTGRES_USER | Phoenix Arize LLM Traces | Phase 06 (obs) |
| `langfuse` | POSTGRES_USER | Langfuse Prompt Management | Phase 06 (obs) |

**Relationships**: None at the database level. Services must not create cross-database
foreign keys. All inter-service communication goes through the API layer.

**Key invariant**: `POSTGRES_DB` (the default database created by the Docker image) is
separate from these six and is used only by postgres internal tooling and health checks.

### Contracts

No external API contracts apply to this feature. The interface this feature exposes is:
- A running postgres service at `postgres:5432` (Docker network hostname)
- Six databases available for connection by service name
- Platform user credentials via `${POSTGRES_USER}` / `${POSTGRES_PASSWORD}`

These are internal Docker network contracts, not HTTP APIs. No contracts/ directory needed.

---

## Complexity Tracking

No constitution violations. No complexity tracking required.
