# Quickstart: Automated Database Provisioning

**Feature**: 003-db-provisioning

---

## Prerequisites

1. `.env` file exists at the repo root with real values for:
   ```
   POSTGRES_USER=<your-username>
   POSTGRES_PASSWORD=<your-password>
   POSTGRES_DB=postgres
   ```
2. Docker (OrbStack or Docker Desktop) is running.
3. You are on branch `003-db-provisioning`.

---

## Fresh-Volume Start

```bash
make up-core
```

This starts the `core` profile services. PostgreSQL will:
1. Initialise a new cluster in the `pg_data` named volume
2. Execute `scripts/init-db.sql` via the init directory
3. Create all six databases and grant privileges
4. Signal healthy via `pg_isready`

---

## Verify Provisioning

```bash
# List all six databases
docker exec $(docker ps -qf name=postgres) \
  psql -U "$POSTGRES_USER" -lqt | awk '{print $1}' | \
  grep -E '^(litellm|keycloak|mlflow|kong|phoenix|langfuse)$'

# Expected output (order may vary):
# keycloak
# kong
# langfuse
# litellm
# mlflow
# phoenix

# Verify platform user can write to each database
for db in litellm keycloak mlflow kong phoenix langfuse; do
  docker exec $(docker ps -qf name=postgres) \
    psql -U "$POSTGRES_USER" -d "$db" \
    -c "CREATE TABLE IF NOT EXISTS _probe (id int); DROP TABLE _probe;" \
    -q && echo "$db OK" || echo "$db FAIL"
done

# Verify postgres health status
docker inspect $(docker ps -qf name=postgres) \
  --format '{{.State.Health.Status}}'
# Expected: healthy
```

---

## Verify Idempotent Restart

```bash
make down
make up-core
# Wait for healthy status, then re-run verification above
# No errors should appear in postgres logs on second start
make logs svc=postgres | grep -i "database\|error\|fatal"
```

---

## View Init Script Logs

```bash
make logs svc=postgres | head -60
# On a fresh volume you will see lines like:
#   /docker-entrypoint-initdb.d/init.sql
# On a restart you will see no init directory output
```

---

## Reset to Fresh State (Destructive)

```bash
make down-v    # Removes pg_data volume — ALL DATA LOST
make up-core   # Provisions from scratch
```

---

## Memory Check

```bash
make stats
# postgres should be well under 200 MB on an idle fresh start
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `FATAL: role "X" does not exist` | `POSTGRES_USER` in `.env` doesn't match the user the init script expected | Wipe volume (`make down-v`) and restart |
| Databases missing after start | Container was killed before init completed; `pg_data` is non-empty but partially initialised | `make down-v && make up-core` |
| `connection refused` from app services | postgres healthcheck not yet passing | Wait 10–15 s; check `docker inspect` health status |
| `permission denied` on table operations | `GRANT` did not execute (partial init) | `make down-v && make up-core` |
