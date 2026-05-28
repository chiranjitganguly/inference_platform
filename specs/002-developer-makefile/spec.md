# Feature Specification: Developer Makefile

**Feature Branch**: `002-developer-makefile`

**Created**: 2026-05-27

**Status**: Draft

---

## Clarifications

### Session 2026-05-27

- Q: Must every make target have a description visible in `make help` output? → A: Yes — every target MUST include a double-hash (`##`) self-documenting comment so that `make help` lists it automatically; no target is undocumented.
- Q: How should Docker Compose be invoked to ensure the `.env` file is always loaded? → A: All Docker Compose invocations MUST go through a `COMPOSE` variable that includes `--env-file .env`, ensuring the project environment file is always applied regardless of how make is invoked.
- Q: Is `svc=` a required argument or an optional one for `restart` and `logs`? → A: `svc=` is required. Omitting it MUST produce a usage error and exit non-zero without performing any action.
- Q: Are the six service-group profiles independent and combinable? → A: Yes — each `up-<group>` target is independent; running multiple targets in sequence leaves all started profiles running simultaneously. `up-all` is the canonical shorthand for starting all six at once.
- Q: Should seed targets be safe to re-run on an already-seeded platform? → A: Yes — seed targets MUST be idempotent; re-running on an already-configured service updates or leaves existing configuration unchanged and never duplicates routes, secrets, or other config.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start a Service Group (Priority: P1)

A developer clones the repo and wants to bring up a specific subset of the platform — for example, only the core services needed for LLM routing, or the full observability stack. They run a single `make` command matching the service group name. Docker Compose starts the correct profile, loads the project environment file automatically, and the developer never types `docker compose` flags.

**Why this priority**: Starting the platform is the first thing every developer does. If there is no make target for it, every developer must memorise profile flags. P1 because nothing else matters if the platform cannot start.

**Independent Test**: Can be fully tested by running `make up-core` on a machine with Docker/OrbStack running and verifying the core-profile containers come up. Delivers a running platform with no flags memorised.

**Acceptance Scenarios**:

1. **Given** the platform is stopped, **When** a developer runs `make up-core`, **Then** all containers in the `core` Compose profile start in detached mode and the command exits 0.
2. **Given** the platform is stopped, **When** a developer runs `make up-obs`, **Then** all containers in the `obs` Compose profile start in detached mode and the command exits 0.
3. **Given** the platform is stopped, **When** a developer runs `make up-auth`, **Then** all containers in the `auth` Compose profile start in detached mode and the command exits 0.
4. **Given** the platform is stopped, **When** a developer runs `make up-safety`, **Then** all containers in the `safety` Compose profile start in detached mode and the command exits 0.
5. **Given** the platform is stopped, **When** a developer runs `make up-gov`, **Then** all containers in the `gov` Compose profile start in detached mode and the command exits 0.
6. **Given** the platform is stopped, **When** a developer runs `make up-portal`, **Then** all containers in the `portal` Compose profile start in detached mode and the command exits 0.
7. **Given** the platform is stopped, **When** a developer runs `make up-all`, **Then** all six Compose profiles start in detached mode and the command exits 0.
8. **Given** `make up-core` has been run, **When** a developer then runs `make up-obs`, **Then** the `obs` profile containers start and the `core` profile containers remain running (both profiles are simultaneously active).
9. **Given** any platform state, **When** a developer runs `make down`, **Then** all running containers are stopped and removed and the command exits 0.

---

### User Story 2 - Restart a Named Service (Priority: P2)

A developer has modified a service configuration (e.g., `config.yaml` for LiteLLM) and wants to restart only that one container without touching others. They run `make restart svc=litellm`. If they forget to specify the service name and just run `make restart`, they receive a usage error message and nothing is restarted.

**Why this priority**: Restarting individual services is the most frequent operation during development. The guard-on-missing-name constraint is safety-critical — an unintended full-platform restart can disrupt long-running background operations.

**Independent Test**: Can be fully tested with `make restart svc=litellm` (restarts one container) and `make restart` alone (prints usage error, exits non-zero, no container touched).

**Acceptance Scenarios**:

1. **Given** the platform is running, **When** a developer runs `make restart svc=litellm`, **Then** only the `litellm` container is restarted and all other containers remain unchanged.
2. **Given** the platform is running, **When** a developer runs `make restart` without specifying `svc=`, **Then** a usage error message is printed, the command exits non-zero, and no container is restarted or stopped.
3. **Given** the platform is running, **When** a developer runs `make restart svc=kong`, **Then** only the `kong` container is restarted.

---

### User Story 3 - Tail Service Logs (Priority: P2)

A developer wants to watch the live log output of a specific service during debugging. They run `make logs svc=kong` and the terminal streams the container logs. If they omit `svc=`, a usage error is printed and no logs are shown.

**Why this priority**: Paired with restart, log-tailing is the core development feedback loop. Equal priority to restart — both together make a complete debugging workflow.

**Independent Test**: Can be fully tested by running `make logs svc=kong` while the platform is running and verifying log output streams to the terminal. `make logs` alone prints a usage error.

**Acceptance Scenarios**:

1. **Given** the platform is running, **When** a developer runs `make logs svc=kong`, **Then** the live log stream for the `kong` container is displayed and the command follows (does not exit until interrupted).
2. **Given** the platform is running, **When** a developer runs `make logs` without `svc=`, **Then** a usage error is printed, the command exits non-zero, and no log output is shown.

---

### User Story 4 - Seed Third-Party Services (Priority: P3)

A developer starts the platform for the first time and needs to configure Kong routes and Vault secrets. They run `make seed-kong` and `make seed-vault` to push the standard configuration into each service. Each is a single command that applies all required configuration.

**Why this priority**: Seeding is required once on first run (or after `make down`). It is a separate workflow from starting the platform so it is lower priority — but without it, the platform cannot route traffic or resolve secrets.

**Independent Test**: Can be fully tested by running `make seed-kong` on a running core stack and verifying Kong admin API reports the expected routes and services.

**Acceptance Scenarios**:

1. **Given** the `core` profile is running and Kong is not yet seeded, **When** a developer runs `make seed-kong`, **Then** Kong is configured with all required routes and services and the command exits 0.
2. **Given** Kong is already seeded, **When** a developer runs `make seed-kong` again, **Then** no routes or services are duplicated, existing configuration is unchanged or updated, and the command exits 0.
3. **Given** the `core` profile is running with Vault unsealed, **When** a developer runs `make seed-vault`, **Then** the required secrets are written to Vault and the command exits 0.
4. **Given** Vault is already seeded, **When** a developer runs `make seed-vault` again, **Then** no secrets are duplicated or corrupted and the command exits 0.

---

### User Story 5 - Run Smoke Tests (Priority: P3)

A developer wants a quick sanity check that the platform is healthy — that routes respond and the LLM proxy returns a valid response. They run `make smoke` and receive a pass/fail result within 30 seconds.

**Why this priority**: Smoke tests are validation, not development. Lower priority than the core operational targets, but required before any PR merge.

**Independent Test**: Can be fully tested by running `make smoke` against a seeded running platform and verifying the command exits 0 when all checks pass.

**Acceptance Scenarios**:

1. **Given** the platform is running and seeded, **When** a developer runs `make smoke`, **Then** all smoke test checks pass and the command exits 0 within 30 seconds.
2. **Given** a service is down, **When** a developer runs `make smoke`, **Then** the specific failing check is reported and the command exits non-zero.

---

### User Story 6 - Observe Platform Status (Priority: P4)

A developer wants a quick status overview — what is running, port bindings, resource consumption. They run `make ps` or `make stats` to see the relevant Compose state without opening a browser or running raw Docker commands.

**Why this priority**: Status and stats are convenience targets — helpful but not blocking any workflow.

**Independent Test**: Can be tested by running `make ps` and verifying it lists containers, ports, and state in a readable format.

**Acceptance Scenarios**:

1. **Given** containers are running, **When** a developer runs `make ps`, **Then** a table of running containers with their ports and status is printed and the command exits 0.
2. **Given** containers are running, **When** a developer runs `make stats`, **Then** live per-container resource consumption metrics (CPU, memory) are displayed continuously until interrupted by the developer.

---

### Edge Cases

- What happens when `make restart` is run without `svc=`? → Usage error printed; exits non-zero; nothing restarted.
- What happens when `make logs` is run without `svc=`? → Usage error printed; exits non-zero; no logs streamed.
- What happens when `make up-core` is run but Docker is not running? → Docker Compose exits non-zero; the make target propagates the error.
- What happens when a seeding target is run before the target service is ready? → Seed script may fail; developer sees the error and must retry.
- What happens when `make down` is run and no containers are running? → Compose exits 0 (nothing to stop); the target is idempotent.
- What happens when `make up-obs` is run while `make up-core` is already running? → The `obs` profile containers are added; `core` containers remain running.
- What happens when `make seed-kong` is run on an already-seeded Kong? → Idempotent — existing routes and services are updated or left unchanged; no duplicates created; exits 0.
- What happens when `make seed-vault` is run on an already-seeded Vault? → Idempotent — existing secrets are updated or left unchanged; exits 0.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Makefile MUST provide a dedicated `up-<group>` target for each of the six service groups: `core`, `obs`, `auth`, `safety`, `gov`, `portal`. Each target operates independently — multiple profiles MAY be active simultaneously.
- **FR-002**: The Makefile MUST provide an `up-all` target that starts all six service groups together in a single command.
- **FR-003**: The Makefile MUST provide a `down` target that stops and removes all running containers.
- **FR-004**: The Makefile MUST provide a `restart` target that accepts a required `svc=<name>` variable and restarts only the named container.
- **FR-005**: The `restart` target MUST print a usage error message and exit non-zero when invoked without `svc=`; it MUST NOT restart or stop any container.
- **FR-006**: The Makefile MUST provide a `logs` target that accepts a required `svc=<name>` variable and tails the logs of the named container.
- **FR-007**: The `logs` target MUST print a usage error message and exit non-zero when invoked without `svc=`; it MUST NOT display any log output.
- **FR-008**: The Makefile MUST provide a `seed-kong` target that applies Kong route and service configuration. The target MUST be idempotent — re-running on an already-seeded Kong instance MUST NOT duplicate routes or services.
- **FR-009**: The Makefile MUST provide a `seed-vault` target that writes required secrets to Vault. The target MUST be idempotent — re-running on an already-seeded Vault instance MUST NOT create duplicate or conflicting entries.
- **FR-010**: The Makefile MUST provide a `smoke` target that runs smoke tests and exits 0 on pass, non-zero on failure.
- **FR-011**: The Makefile MUST provide a `ps` target that displays running containers with their ports and status.
- **FR-012**: The Makefile MUST provide a `stats` target that displays live, per-container resource consumption metrics (CPU and memory) continuously until interrupted.
- **FR-013**: All targets that invoke Docker Compose MUST ensure the project `.env` file is automatically loaded without requiring the developer to supply any flags. No developer-facing target may require manual flag entry.
- **FR-014**: The Makefile MUST include a `help` target set as the default goal. Every make target MUST have a self-documenting description (via double-hash `##` comment) so that `make help` lists all available targets with one-line descriptions.
- **FR-015**: The Makefile MUST be located at the project root (`Makefile`).
- **FR-016**: All Docker Compose invocations inside the Makefile MUST consistently apply the project environment file so that service configuration variables are always resolved from the same source.

### Key Entities

- **Service group**: A named subset of the platform (`core`, `obs`, `auth`, `safety`, `gov`, `portal`) corresponding to a Docker Compose profile.
- **Named service**: A single Docker Compose service (container) identified by its service name (e.g., `litellm`, `kong`, `guardrails`).
- **Make target**: A named entry point in the Makefile that a developer invokes via `make <target>`.
- **Seed script**: A shell script or inline command sequence that pushes initial configuration to a third-party service (Kong or Vault) via their respective APIs.
- **Smoke test**: A lightweight end-to-end probe that verifies the platform is reachable and returns expected responses.
- **Environment file**: The project-root `.env` file that supplies all service configuration variables at runtime.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every developer operation listed in CLAUDE.md can be completed with a single `make <target>` command — no developer needs to type `docker compose` directly.
- **SC-002**: Running `make restart` without `svc=` prints a usage error and exits non-zero in under 1 second, with no container state change.
- **SC-003**: Running `make logs` without `svc=` prints a usage error and exits non-zero in under 1 second, with no log output produced.
- **SC-004**: Each `up-<group>` target starts the correct profile and exits 0 in under 30 seconds on a machine with images already pulled.
- **SC-005**: `make smoke` completes (pass or fail) in under 30 seconds on a running, seeded platform.
- **SC-006**: Running `make` (no target) or `make help` lists all targets with one-line descriptions and exits 0 in under 1 second.
- **SC-007**: Running `make down` when no containers are running exits 0 (idempotent).
- **SC-008**: Running `make up-core` followed by `make up-obs` results in containers from both profiles running simultaneously, with no containers from `core` stopped.
- **SC-009**: Running `make seed-kong` or `make seed-vault` a second time on an already-seeded platform exits 0 with no duplicate configuration created.

---

## Assumptions

- The project uses Docker Compose with named profiles (`core`, `obs`, `auth`, `safety`, `gov`, `portal`) defined in the project's `docker-compose.yml`.
- OrbStack is the Docker runtime; `docker compose` (V2 plugin syntax) is the Compose CLI — not the legacy `docker-compose` binary.
- Seed scripts for Kong and Vault exist as shell scripts in the `scripts/` directory or will be created as part of this feature.
- Smoke tests are implemented as shell script probes (`curl` health checks) — no external test runner required.
- GNU Make 3.81+ is available on all developer machines (ships with macOS Xcode Command Line Tools).
- The Makefile does not manage OrbStack lifecycle — `scripts/setup-mac.sh` handles that.
- The `restart` and `logs` targets operate on named services using the Compose service name, not the container ID.
- macOS 14+ with OrbStack is the only supported developer platform — Windows and Linux are out of scope.
