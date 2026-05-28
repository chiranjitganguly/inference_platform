# Data Model: Automated Database Provisioning

**Feature**: 003-db-provisioning | **Date**: 2026-05-28

---

## Scope

This feature creates database-level namespaces only. No tables, indexes, sequences, or
extensions are created by `init-db.sql`. Each service's ORM or migration tool creates its
own schema on first connection.

---

## Logical Database Map

| Database | Postgres Owner | Connecting Service | Profile |
|---|---|---|---|
| `litellm` | `$POSTGRES_USER` | LiteLLM Proxy | core |
| `keycloak` | `$POSTGRES_USER` | Keycloak | auth |
| `mlflow` | `$POSTGRES_USER` | MLflow | gov |
| `kong` | `$POSTGRES_USER` | Kong (DB mode) | core |
| `phoenix` | `$POSTGRES_USER` | Phoenix Arize | obs |
| `langfuse` | `$POSTGRES_USER` | Langfuse Server + Worker | obs |

---

## Platform User

| Attribute | Value |
|---|---|
| Username | `$POSTGRES_USER` (resolved at runtime from `.env`) |
| Role | PostgreSQL superuser (created by Docker image entrypoint) |
| Privileges on each DB | ALL PRIVILEGES (owner + explicit grant) |
| Shared by | All six application services — no per-service users |

**Invariant**: No service uses a separate database user. Credential rotation means
updating `POSTGRES_USER` / `POSTGRES_PASSWORD` in `.env` (and Vault in production).

---

## Volume

| Attribute | Value |
|---|---|
| Name | `pg_data` |
| Type | Docker named volume |
| Mount point | `/var/lib/postgresql/data` |
| Lifecycle | Created by `make up-core`; destroyed by `make down-v` |

**Warning**: `make down-v` removes `pg_data` and all data within it. This is intentional
for local dev resets but is destructive. See quickstart.md for reset procedure.

---

## Init Script Entity

| Attribute | Value |
|---|---|
| Source path | `scripts/init-db.sql` |
| Mounted path | `/docker-entrypoint-initdb.d/init.sql` |
| Execution trigger | First start only (empty `$PGDATA`) |
| Execution user | `$POSTGRES_USER` (superuser) |
| Idempotency | Built-in (init dir skipped on non-empty `$PGDATA`) + per-DB existence guards |

---

## Relationships

```
postgres:16-alpine (service)
    └── pg_data (named volume) → /var/lib/postgresql/data
    └── init-db.sql (bind mount :ro) → /docker-entrypoint-initdb.d/init.sql
    └── creates on first start:
            ├── database: litellm    (owner: POSTGRES_USER)
            ├── database: keycloak   (owner: POSTGRES_USER)
            ├── database: mlflow     (owner: POSTGRES_USER)
            ├── database: kong       (owner: POSTGRES_USER)
            ├── database: phoenix    (owner: POSTGRES_USER)
            └── database: langfuse   (owner: POSTGRES_USER)
```

No cross-database foreign keys. No shared tables. Databases are isolated namespaces.
