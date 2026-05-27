# Feature Specification: Developer Environment Setup Script

**Feature Branch**: `001-dev-env-setup`

**Created**: 2026-05-26

**Status**: Draft

**Input**: User description: "Build a project setup script for the AI inference platform that prepares a developer environment in under 5 minutes. When a developer runs the setup script for the first time on a Mac with OrbStack installed it must verify that OrbStack is running, check available free memory, and create a .env configuration file from the template if one does not already exist. The script must exit with a clear success message when all checks pass. When OrbStack is absent it must exit with a non-zero code and print the brew install instruction. When free memory is below 3 GB it should print a warning but still complete and create the .env file. A pre-existing .env must never be overwritten."

---

## Clarifications

### Session 2026-05-26

- Q: Can the script overwrite an existing `.env` file? → A: Never — the script must never overwrite an existing `.env` file under any circumstances.
- Q: Must `.env` be in `.gitignore` before the file is created? → A: Yes — the script must verify and add `.env` to `.gitignore` before creating `.env`.
- Q: What values are permitted in `.env.example`? → A: All variable names must have empty values only; no real secrets or credentials may be committed in `.env.example`.
- Q: How is free memory measured and what is the exact threshold? → A: Free memory is measured using `vm_stat` and the threshold is exactly 3000 MB.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - First-Time Environment Setup (Priority: P1)

A new developer clones the repository and runs the setup script for the first time on a Mac
that already has OrbStack installed and running, with sufficient free memory. The script
validates their environment, creates a `.env` file from the template, and exits cleanly with
a success message so they can immediately proceed to starting the platform.

**Why this priority**: This is the happy-path that every new team member must experience.
It unblocks all subsequent development work and is the most common execution scenario.

**Independent Test**: Run the script on a clean Mac with OrbStack running and no existing
`.env` file. The script completes in under 5 minutes, a `.env` file is created, and a
success message is printed with exit code 0.

**Acceptance Scenarios**:

1. **Given** a Mac with OrbStack installed and running, no `.env` file present, and ≥ 3000 MB
   free memory, **When** the developer runs the setup script, **Then** the script prints a
   confirmation that OrbStack is running, confirms memory is sufficient, ensures `.env` is
   in `.gitignore`, creates `.env` from the template, prints a clear success message, and
   exits with code 0.

2. **Given** the script has already been run and a `.env` file exists, **When** the developer
   runs the setup script again, **Then** the existing `.env` is not modified, the script
   notes that `.env` already exists, and exits with code 0.

---

### User Story 2 - Low Memory Warning Path (Priority: P2)

A developer runs the setup script on a Mac with less than 3 GB of free memory (e.g., running
many other applications). The script warns them about the memory situation but still completes
the setup so they are not blocked.

**Why this priority**: Developers should not be hard-blocked by a memory warning — they may
still want to proceed and know the risk. Setup completion is more important than preventing
the environment from being configured.

**Independent Test**: Run the script on a system with simulated low free memory (< 3000 MB,
measured via `vm_stat`). The script prints a warning about memory, still creates the `.env`
file, and exits with code 0.

**Acceptance Scenarios**:

1. **Given** a Mac with OrbStack running and < 3000 MB free memory (as reported by `vm_stat`),
   no `.env` present,
   **When** the developer runs the setup script, **Then** the script prints a warning
   message about available memory being below the recommended threshold, continues
   execution, creates the `.env` file, and exits with code 0.

2. **Given** OrbStack is running and memory is low, **When** the script completes,
   **Then** the warning is clearly labelled (e.g., `[WARNING]`) and the success message
   is still printed so the developer knows setup completed.

---

### User Story 3 - OrbStack Not Installed (Priority: P3)

A developer runs the setup script on a Mac where OrbStack is not installed. The script
detects the absence, tells them exactly how to install it, and exits immediately with a
non-zero code so automated tooling can detect the failure.

**Why this priority**: This is a hard blocker — the platform cannot run without OrbStack.
The failure path must be informative and not leave the developer guessing.

**Independent Test**: Run the script on a system without OrbStack. The script prints the
`brew install` instruction, does not create a `.env` file, and exits with a non-zero code.

**Acceptance Scenarios**:

1. **Given** OrbStack is not installed on the Mac, **When** the developer runs the setup
   script, **Then** the script prints an error message identifying OrbStack as missing,
   prints the exact `brew install --cask orbstack` command, and exits with a non-zero
   exit code (≥ 1).

2. **Given** OrbStack is not installed, **When** the script exits, **Then** no `.env` file
   is created (setup is not partially completed).

---

### User Story 4 - OrbStack Installed but Not Running (Priority: P4)

A developer has OrbStack installed but it is not currently running. The script detects that
OrbStack is present but not active, prints an actionable message telling them to start it,
and exits with a non-zero code.

**Why this priority**: A common mistake after reboot or first install. Distinct from
"not installed" — the fix is different (start the app, not install it).

**Independent Test**: Quit OrbStack on a Mac where it is installed, then run the script.
The script prints a "start OrbStack" message and exits non-zero without creating a `.env`.

**Acceptance Scenarios**:

1. **Given** OrbStack is installed but not running, **When** the developer runs the setup
   script, **Then** the script prints a message that OrbStack is installed but not running
   and instructs the developer to open or start it, then exits with a non-zero exit code.

---

### Edge Cases

- What happens when the `.env.example` template file does not exist in the repository?
  The script must exit with a non-zero code and a clear error message rather than creating
  an empty `.env`.
- What happens when the script is run on a non-Mac operating system (Linux, Windows)?
  The script should exit with a clear "Mac only" message and non-zero code.
- What happens when the user does not have write permission to the project directory?
  The script should detect the failure to create `.env` and report a permission error.
- What happens if OrbStack is partially installed (binary present but daemon not
  responding)?  Treat the same as "installed but not running" (User Story 4).
- What happens when `.env.example` contains a non-empty value (e.g., a real API key)?
  The script must exit with a non-zero code and a security warning, never copying the
  file to `.env`.
- What happens when `.gitignore` does not exist at all in the project root?
  The script must create `.gitignore` with a `.env` entry before proceeding.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The script MUST verify that OrbStack is installed on the Mac before
  proceeding with any other checks.
- **FR-002**: The script MUST verify that OrbStack is actively running after confirming
  it is installed.
- **FR-003**: When OrbStack is not installed, the script MUST print the exact
  `brew install --cask orbstack` command and exit with a non-zero code.
- **FR-004**: When OrbStack is installed but not running, the script MUST print an
  instruction to start OrbStack and exit with a non-zero code.
- **FR-005**: The script MUST check the amount of free memory available on the system
  using `vm_stat`. The threshold is exactly 3000 MB of free memory.
- **FR-006**: When free memory is below 3000 MB, the script MUST print a clearly labelled
  `[WARNING]` message but MUST NOT exit — it must continue and complete setup.
- **FR-007**: The script MUST create a `.env` file by copying `.env.example` when no
  `.env` file exists in the project root.
- **FR-008**: The script MUST NOT overwrite or modify an existing `.env` file under any
  circumstances, regardless of its content or age. It must print a notice that the file
  already exists and skip creation entirely.
- **FR-009**: When the `.env.example` template file is absent, the script MUST exit with
  a non-zero code and an informative error message.
- **FR-010**: When all checks pass and `.env` is created (or already exists), the script
  MUST print a clear success message and exit with code 0.
- **FR-011**: The entire setup sequence MUST complete in under 5 minutes on a standard
  Mac development machine with a normal internet connection.
- **FR-012**: The script MUST be executable on macOS without requiring any additional
  tool installation beyond what is already required for the platform.
- **FR-013**: Before creating the `.env` file, the script MUST verify that `.env` is
  listed in `.gitignore`. If it is absent from `.gitignore`, the script MUST add a `.env`
  entry to `.gitignore` before proceeding with `.env` creation.
- **FR-014**: The `.env.example` file MUST contain only empty values for all variable
  names (e.g., `API_KEY=` with nothing after the `=`). No real credentials, tokens, or
  secrets may appear in `.env.example`. The script MUST validate this and exit with a
  non-zero code and a security warning if any non-empty value is detected.

### Key Entities

- **Setup script**: The single executable file run by the developer. Accepts no required
  arguments. Produces console output and a `.env` file as side effects.
- **`.env.example`**: The template file committed to the repository containing all required
  environment variable names with strictly empty values (e.g., `API_KEY=`). No real
  credentials or secrets may appear in this file. Read-only input to the script; validated
  by the script before copying.
- **`.env`**: The developer-local configuration file created from `.env.example`. Must be
  listed in `.gitignore` — never committed to source control. Created once by the script;
  never overwritten under any circumstances.
- **OrbStack**: The container runtime required to run the platform locally. Checked for
  presence and running state.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A developer with a correctly configured Mac can complete the entire setup
  sequence in under 5 minutes from a fresh repository clone.
- **SC-002**: 100% of runs on a correctly configured Mac (OrbStack running, ≥ 3000 MB free
  memory as measured by `vm_stat`, no existing `.env`) exit with code 0 and produce a
  populated `.env` file with `.env` confirmed present in `.gitignore`.
- **SC-003**: 100% of runs where OrbStack is absent exit with a non-zero code and include
  the `brew install --cask orbstack` instruction in the output.
- **SC-004**: 0% of runs overwrite an existing `.env` file under any execution scenario.
- **SC-005**: A developer encountering any failure condition can identify the exact
  remediation step without consulting any external documentation.
- **SC-006**: Runs with free memory below 3000 MB complete successfully (exit code 0) with
  a visible `[WARNING]` message, and produce a `.env` file.
- **SC-007**: 100% of runs detect and reject a `.env.example` file containing any non-empty
  variable value, exiting with a non-zero code and a security warning before any `.env`
  file is created.

---

## Assumptions

- The target platform is macOS only. Linux and Windows support is explicitly out of scope
  for this version.
- OrbStack is the only supported container runtime for local development. Docker Desktop
  is not a supported alternative for this script's checks.
- The `.env.example` file exists at the project root and is maintained alongside the
  codebase. The script is not responsible for creating or updating `.env.example`.
- Homebrew (`brew`) is available on the developer's Mac — it is a standard prerequisite
  for the project and the OrbStack install instruction assumes it.
- The script runs from the project root directory. Relative paths (`.env`, `.env.example`)
  are resolved from the working directory at invocation time.
- No network calls are required by the setup script itself. All checks are local.
- The script does not start OrbStack automatically — it only checks state and guides the
  developer. Automated start is out of scope.
- "Free memory" means pages free as reported by `vm_stat`, converted to MB. The threshold
  is exactly 3000 MB. Swap space is not counted.
- `.env.example` is considered invalid if any line matches the pattern `KEY=<non-empty-value>`.
  Comment lines (starting with `#`) and blank lines are ignored during validation.
- The `.gitignore` check covers only the project root `.gitignore`. Global gitignore
  configurations are not considered sufficient.
