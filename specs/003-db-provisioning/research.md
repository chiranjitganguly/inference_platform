# Research: Automated Database Provisioning

**Feature**: 003-db-provisioning | **Date**: 2026-05-28

All NEEDS CLARIFICATION items resolved. No open unknowns.

---

## Decision Log

See `plan.md` §Phase 0 for full rationale. Summary:

| Decision | Choice | Key Reason |
|---|---|---|
| Idempotency mechanism | PostgreSQL init directory + existence guards | Built-in, zero extra containers |
| Conditional CREATE syntax | `\gexec` meta-command | Only valid pattern in pure `.sql` without extensions |
| GRANT target | `CURRENT_USER` | Portable across environments; self-documenting |
| Volume strategy | Named volume `pg_data` + `:ro` bind-mount | Docker lifecycle management + immutable source |
| Healthcheck | `pg_isready` | Zero-dependency, ships in `postgres:16-alpine` |

---

## PostgreSQL Init Directory Behaviour (postgres:16-alpine)

The Docker entrypoint script checks whether `$PGDATA` (`/var/lib/postgresql/data`) is
empty. If empty, it initialises the cluster and then executes all files in
`/docker-entrypoint-initdb.d/` in alphabetical order. Files ending in `.sql` are piped to
`psql`; files ending in `.sh` are sourced. If `$PGDATA` is not empty, the init directory
is skipped entirely.

**Implication for idempotency**: A normal `docker compose restart` or `make down && make up-core`
against an existing `pg_data` volume will never re-execute `init.sql`. The init directory
approach is idempotent across restarts by design, not by script logic.

**Implication for partial init**: If the container is killed after cluster init but before
all SQL statements complete, `$PGDATA` will be non-empty on the next start and the init
directory will be skipped. The `\gexec` existence guards handle the case where init scripts
might be re-run (e.g., in a test environment that manually calls initdb), but are not the
primary idempotency mechanism for normal operations.

---

## `\gexec` Pattern — Verified Syntax

```sql
-- Conditional database creation for PostgreSQL 16
-- \gexec executes the query result as a SQL command.
-- If the SELECT returns no rows (db exists), nothing is executed.
SELECT format('CREATE DATABASE %I', 'litellm')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'litellm')\gexec
```

`format('CREATE DATABASE %I', name)` uses `%I` (identifier quoting) rather than `%s` to
safely handle database names that might match SQL reserved words. For the six names in this
feature (`litellm`, `keycloak`, `mlflow`, `kong`, `phoenix`, `langfuse`) this is a no-op
because none are reserved, but the pattern is correct and defensive.

---

## `CURRENT_USER` in GRANT — PostgreSQL 16

```sql
GRANT ALL PRIVILEGES ON DATABASE litellm TO CURRENT_USER;
```

`CURRENT_USER` is a system function that resolves to the name of the currently executing
role. In a `GRANT` target list it is treated as a role name. In PostgreSQL 16, this is
valid syntax. The init script runs as `$POSTGRES_USER`, so this statement grants all
privileges on `litellm` to `$POSTGRES_USER`.

Since `CREATE DATABASE` makes the executing role the owner, this grant is logically
redundant. It is retained for explicit documentation of intent and forward compatibility.

---

## Healthcheck Command

```yaml
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
  interval: 5s
  timeout: 5s
  retries: 10
  start_period: 10s
```

Note: `$$POSTGRES_USER` uses Docker Compose double-dollar escaping. The variable is
resolved at container runtime from the container's environment, not at Compose parse time.

`start_period: 10s` gives postgres time to start and run init scripts before the retry
count begins, preventing false-negative health failures on a fresh volume where
`init.sql` is running.

`retries: 10` with `interval: 5s` provides a 50-second window — sufficient for the
30-second fresh-volume provisioning target (SC-001) with headroom.

---

## No Remaining Unknowns

All NEEDS CLARIFICATION items resolved:

- ✅ Idempotency: init directory mechanism + existence guards
- ✅ Conditional CREATE: `\gexec` pattern
- ✅ GRANT target: `CURRENT_USER`
- ✅ Volume naming: `pg_data`
- ✅ Healthcheck: `pg_isready`
- ✅ Startup ordering: `depends_on: postgres: condition: service_healthy` in downstream services
