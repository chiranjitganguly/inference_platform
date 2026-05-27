# Research: Developer Environment Setup Script

**Feature**: `001-dev-env-setup`
**Date**: 2026-05-26
**Status**: Complete — all decisions resolved

---

## Decision 1: OrbStack Detection via `orbctl`

**Decision**: Use a two-step check — `command -v orbctl` for install detection, then
`orbctl status` for running state.

**Rationale**:
- `command -v orbctl` is POSIX-portable and does not launch the binary. Returns non-zero
  if not in PATH, which means OrbStack was never installed or the app bundle is present but
  PATH is not configured (treated as "not installed").
- `orbctl status` exits 0 when the OrbStack daemon is healthy and running; exits non-zero
  when the app is installed but not running (e.g., after reboot). This maps cleanly to
  User Stories 3 and 4.
- OrbStack ships `orbctl` at `/usr/local/bin/orbctl` (Intel) and
  `/opt/homebrew/bin/orbctl` (Apple Silicon). Both paths are in the standard Homebrew PATH
  that macOS 14+ sets up.

**Alternatives considered**:
- Checking `/Applications/OrbStack.app` existence: detects install but cannot distinguish
  running vs. stopped. Rejected.
- Checking the Docker socket (`/var/run/docker.sock`): Docker Desktop can also create this
  socket; ambiguous when both are present. Rejected.
- `pgrep -x OrbStack`: process name check. Fragile across OrbStack versions. Rejected.

**Implementation pattern**:
```bash
if ! command -v orbctl &>/dev/null; then
    echo "[ERROR] OrbStack is not installed."
    echo "        Install with: brew install --cask orbstack"
    exit 1
fi

if ! orbctl status &>/dev/null 2>&1; then
    echo "[ERROR] OrbStack is installed but not running."
    echo "        Open OrbStack.app to start it."
    exit 1
fi
```

---

## Decision 2: Free Memory Measurement via `vm_stat`

**Decision**: Use `vm_stat` + `sysctl hw.pagesize` to calculate free RAM in MB, compare
against threshold of 3000 MB.

**Rationale**:
- `vm_stat` is a macOS built-in that reports memory in pages. It reports `Pages free`
  (unallocated) and `Pages speculative` (prefetched but readily reclaimable). For a warning
  threshold, "Pages free" alone is conservative and appropriate — it represents genuinely
  available RAM before any reclamation.
- Page size varies by hardware: 4096 bytes (4 KB) on Intel Macs; 16384 bytes (16 KB) on
  Apple Silicon. Using `sysctl -n hw.pagesize` is required for portability across both.
- Integer arithmetic in Bash avoids any floating-point dependency.

**Alternatives considered**:
- `top -l 1 | grep PhysMem`: parses human-readable output with units (GB/MB) that vary by
  system locale. Fragile. Rejected.
- `memory_pressure`: reports pressure level (normal/warn/critical) but no absolute MB value.
  Insufficient for a precise 3000 MB threshold. Rejected.
- `sysctl vm.page_free_count` (Linux-style): not available on macOS. Rejected.

**Implementation pattern**:
```bash
page_size=$(sysctl -n hw.pagesize 2>/dev/null || echo 16384)
free_pages=$(vm_stat | awk '/^Pages free:/ { gsub(/\.$/, "", $3); print $3 }')
free_mb=$(( free_pages * page_size / 1048576 ))

if [[ $free_mb -lt 3000 ]]; then
    echo "[WARNING] Free memory is ${free_mb} MB (recommended: 3000 MB)."
    echo "          Setup will continue but platform performance may be affected."
fi
```

---

## Decision 3: macOS Version Gate

**Decision**: Use `sw_vers -productVersion` and integer comparison on the major version
component to enforce macOS 14+ requirement.

**Rationale**:
- `sw_vers` is a macOS built-in with stable output format (`14.5`, `15.0`, etc.) since
  macOS 10.x. Splitting on `.` and taking the first component is reliable.
- The version gate fails fast before any other check, giving the developer an immediate and
  clear message.

**Implementation pattern**:
```bash
os_version=$(sw_vers -productVersion)
os_major=$(echo "$os_version" | cut -d. -f1)

if [[ "$os_major" -lt 14 ]]; then
    echo "[ERROR] macOS 14.0+ required. Detected: $os_version"
    exit 1
fi
```

---

## Decision 4: `.env.example` Secret Validation

**Decision**: Scan `.env.example` for lines matching `KEY=<non-empty-value>` after stripping
comments and blank lines. Any match is treated as a secret leak and the script exits non-zero.

**Rationale**:
- A `.env.example` line is safe if and only if the value portion (after `=`) is empty.
  Pattern: `^[A-Za-z_][A-Za-z0-9_]*=.+` matches a non-empty assignment.
- Comment lines (starting with `#`) and blank lines are explicitly ignored.
- This check runs before any file is created, so a secrets violation is always a clean abort.

**Edge cases**:
- `KEY= ` (trailing space): treated as non-empty. Conservative — correct behaviour.
- `KEY=""` (empty string literal): treated as non-empty. Conservative — correct behaviour;
  actual empty should be written as `KEY=` with nothing after `=`.

**Implementation pattern**:
```bash
if grep -vE '^\s*#|^\s*$' .env.example | grep -qE '^[A-Za-z_][A-Za-z0-9_]*=.+'; then
    echo "[ERROR] .env.example contains non-empty values."
    echo "        Remove all secrets before committing .env.example."
    exit 1
fi
```

---

## Decision 5: `.gitignore` Guard

**Decision**: Check for a `.env` entry in the project-root `.gitignore` using `grep -qxF`.
If absent (or if `.gitignore` does not exist), append `.env` before creating the `.env` file.

**Rationale**:
- `grep -qxF '.env'` matches the exact line `.env` (no regex interpretation, full-line match).
  This avoids false positives from `*.env` glob patterns while still catching a plain `.env`
  line.
- `echo ".env" >> .gitignore` creates `.gitignore` if it does not exist (shell redirection
  append creates the file on macOS). This handles the edge case where `.gitignore` is missing.
- The guard runs immediately before `.env` creation so the file is never briefly unprotected.

**Implementation pattern**:
```bash
if ! grep -qxF '.env' .gitignore 2>/dev/null; then
    echo "[INFO] Adding .env to .gitignore..."
    echo ".env" >> .gitignore
fi
```

---

## Decision 6: Script Exit Code Conventions

**Decision**: Use exit code `0` for success (including low-memory warning path); use exit
code `1` for all hard failures (OrbStack absent, OrbStack stopped, template missing, secrets
in template, macOS version gate, write permission failure).

**Rationale**:
- A single non-zero code (`1`) is sufficient for CI/CD and developer tooling to detect
  failure without needing to distinguish individual error types programmatically — the
  printed message provides the human-readable distinction.
- The low-memory path exits `0` because setup completed successfully; the warning is
  informational, not a failure.

---

## Decision 7: `.env` Creation via `cp`

**Decision**: Use `cp .env.example .env` to create the `.env` file. Guard with a pre-check
for `.env` existence; never use `cp -f` or similar overwrite flags.

**Rationale**:
- `cp` preserves file permissions and is universally available on macOS.
- The existence guard (`[[ ! -f .env ]]`) combined with the strict "no overwrite" requirement
  makes `cp` safe — there is no scenario where it overwrites an existing file under normal
  operation.
- Capturing `cp` exit code and reporting a permission error on failure satisfies the edge
  case where the directory is read-only.

**Implementation pattern**:
```bash
if [[ -f .env ]]; then
    echo "[INFO] .env already exists — skipping creation."
else
    if ! cp .env.example .env; then
        echo "[ERROR] Failed to create .env (check directory permissions)."
        exit 1
    fi
    echo "[OK] Created .env from .env.example."
fi
```
