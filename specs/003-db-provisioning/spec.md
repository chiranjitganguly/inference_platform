# Feature Specification: Automated Database Provisioning

**Feature Branch**: `003-db-provisioning`

**Created**: 2026-05-28

**Status**: Draft

**Input**: User description: "Build an automated database provisioning script that creates all required application databases inside a shared PostgreSQL instance on its first startup. The databases needed are litellm, keycloak, mlflow, kong, phoenix, and langfuse. The platform user must receive full privileges on all databases. Provisioning must run automatically on a fresh data volume and be safe to skip on subsequent starts when data already exists."

---

## Clarifications

### Session 2026-05-28

- Q: Is idempotency a hard requirement — must a container restart against an existing volume never fail? → A: Yes — idempotency is a hard requirement. A container restart against an existing volume must never produce an error or failure from the provisioning step.
- Q: Is the same database user account used by all application services? → A: Yes — a single shared platform user account is used by all six application services.
- Q: When does the init script execute relative to application containers? → A: The init script runs before any application container starts; startup ordering is enforced at the infrastructure level, not by application-level retry.
- Q: Why are Phoenix and Langfuse databases included given they are observability stores? → A: Phoenix and Langfuse databases are provisioned now to support Phase 06 services that depend on them; their inclusion is intentional and phase-driven.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — First-Time Platform Setup (Priority: P1)

A platform operator starts the inference platform on a fresh machine with no prior data. They run a single start command and expect all six application databases to exist and be accessible to the platform user without any manual database configuration steps.

**Why this priority**: Without this story, no service can start successfully on a new environment — it is the foundational provisioning step every other feature depends on.

**Independent Test**: Can be fully tested by wiping the data volume, starting the database service, and verifying all six databases exist with the correct owner/privileges — without running any other platform service.

**Acceptance Scenarios**:

1. **Given** a host with no existing database data volume, **When** the database service starts for the first time, **Then** all six databases (`litellm`, `keycloak`, `mlflow`, `kong`, `phoenix`, `langfuse`) are created automatically before any application service connects.
2. **Given** all six databases have been created, **When** the platform user attempts to connect to any of them, **Then** the connection succeeds and the user has full read/write/create privileges within that database.
3. **Given** the provisioning completes, **When** the operator checks the database service logs, **Then** each database creation is confirmed with a success entry and no errors are present.

---

### User Story 2 — Idempotent Restart (Priority: P2)

A platform operator restarts the database service after a prior successful start. They expect the provisioning step to detect that the databases already exist and exit cleanly without error, data loss, or duplicate-object conflicts.

**Why this priority**: Any non-idempotent provisioning approach would break routine operations (container restarts, rolling updates, system reboots) and cause service failures on every restart after the first.

**Independent Test**: Can be fully tested by starting the database service a second time after a successful first start and confirming it reaches a healthy state with zero provisioning errors in the logs.

**Acceptance Scenarios**:

1. **Given** all six databases already exist from a prior start, **When** the database service restarts, **Then** the provisioning step detects the existing databases and skips creation without any error or warning.
2. **Given** the provisioning skip occurs, **When** the operator inspects the database service logs, **Then** the log shows a "databases already provisioned, skipping" or equivalent message and the service continues to a healthy state.
3. **Given** a partial provision state (some databases exist, others do not), **When** the database service starts, **Then** only the missing databases are created and already-existing ones are skipped individually.

---

### User Story 3 — Privilege Verification (Priority: P3)

A platform operator or CI pipeline verifies that the platform user holds exactly the required privileges on each database, ensuring downstream services will not encounter permission errors at runtime.

**Why this priority**: Incorrect privilege assignment is a silent failure mode that only surfaces when a specific service attempts a privileged operation — catching it at provisioning time eliminates hard-to-diagnose runtime failures.

**Independent Test**: Can be fully tested by querying the privilege catalog after provisioning and confirming the platform user has create/read/write/delete access on all six databases.

**Acceptance Scenarios**:

1. **Given** provisioning has completed, **When** each application service connects using the platform user credentials, **Then** the service can create tables, insert rows, update rows, and delete rows without permission errors.
2. **Given** the platform user exists, **When** privilege grants are applied during provisioning, **Then** the grants are applied idempotently — re-running provisioning does not produce duplicate-grant errors.

---

### Edge Cases

- What happens when the database service starts but the superuser account is unavailable at provisioning time?
- How does the system handle a provisioning script that is interrupted mid-run (e.g., container OOM kill)?
- What if a database name collides with a reserved system database name?
- How does provisioning behave when the data volume is mounted read-only by accident?
- What if the platform user account does not yet exist when the privilege grant step runs?

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST automatically execute the provisioning script when the database service starts with a fresh (empty) data volume, without requiring operator intervention.
- **FR-002**: The provisioning script MUST create the following databases if they do not exist: `litellm`, `keycloak`, `mlflow`, `kong`, `phoenix`, `langfuse`.
- **FR-003**: The provisioning script MUST grant the platform user full privileges on each of the six databases.
- **FR-004**: The provisioning script MUST be idempotent — running it on a data volume where the databases already exist MUST NOT produce errors, duplicate objects, or data loss.
- **FR-005**: Each database MUST be checked for existence independently, so that a partial prior provisioning run can be completed without re-running already successful steps.
- **FR-006**: The provisioning script MUST log a clear success or skip message for each database so operators can confirm provisioning state from container logs.
- **FR-007**: The database service MUST signal a healthy state only after provisioning completes successfully. No application container MUST be permitted to start until this healthy signal is received — startup ordering is enforced at the infrastructure level, not via application-level retry.
- **FR-008**: If the provisioning script encounters an unrecoverable error (e.g., cannot connect to the database), it MUST exit with a non-zero status and emit a descriptive error message to the log.

### Key Entities

- **Database**: One of the six named application databases (`litellm`, `keycloak`, `mlflow`, `kong`, `phoenix`, `langfuse`). Each is an independent logical namespace within the shared PostgreSQL instance.
- **Platform User**: The single shared database user account used by all six application services — no service-specific users exist. Holds full privileges on all six databases.
- **Provisioning Script**: The automation that runs at database service startup to ensure all required databases and privilege assignments are in place.
- **Data Volume**: The persistent storage mount backing the PostgreSQL instance. Fresh = no prior PostgreSQL cluster; existing = cluster already initialised.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a fresh data volume, all six databases are present and accessible within 30 seconds of the database service reaching a healthy state — with zero manual steps from the operator.
- **SC-002**: On a subsequent restart, the database service reaches a healthy state in the same time budget as a start with no provisioning work to do — provisioning overhead on restart is under 2 seconds.
- **SC-003**: 100% of the platform application services connect to their respective databases without permission errors immediately after provisioning on a fresh volume.
- **SC-004**: Re-running the provisioning script on an already-provisioned volume produces zero errors and zero warnings in the service log.
- **SC-005**: A partial provisioning state (N of 6 databases created) is detected and completed correctly on the next start, with only the missing databases created.

---

## Assumptions

- A single shared PostgreSQL instance hosts all six databases on the same port; no multi-host topology is in scope.
- The platform user account is created outside this feature (e.g., via the `POSTGRES_USER` environment variable) before the provisioning script runs.
- The provisioning script is mounted and executed as part of the standard PostgreSQL container startup mechanism (e.g., init scripts directory), not as a separate sidecar or job.
- The superuser credentials used during provisioning are available to the script via environment variables already present in the container at startup.
- Docker Compose is the runtime for local development; the same provisioning mechanism must work in any environment where PostgreSQL container init scripts are supported.
- The six database names are fixed and not configurable at runtime — changes to the list require a spec update.
- The `phoenix` and `langfuse` databases are provisioned proactively to support Phase 06 services; they are intentionally included even though Phase 06 services are not yet running.
- No schema migrations or seed data are in scope; this feature covers database and privilege creation only.
