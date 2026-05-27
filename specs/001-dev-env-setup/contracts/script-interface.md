# Contract: `scripts/setup-mac.sh` Interface

**Feature**: `001-dev-env-setup`
**Date**: 2026-05-26

---

## Invocation

```bash
bash scripts/setup-mac.sh
# or, if executable bit is set:
./scripts/setup-mac.sh
```

**Arguments**: None. The script accepts no positional arguments or flags.

**Working directory**: Must be run from the project root (the directory containing
`.env.example` and `.gitignore`).

---

## Exit Codes

| Code | Meaning | Scenario |
|---|---|---|
| `0` | Success — environment ready | All checks passed; `.env` created or already existed |
| `0` | Success with warning | All checks passed; memory below 3000 MB (`[WARNING]` printed) |
| `1` | macOS version too old | `sw_vers` reports major version < 14 |
| `1` | OrbStack not installed | `orbctl` not found in PATH |
| `1` | OrbStack not running | `orbctl status` returns non-zero |
| `1` | `.env.example` missing | File not found at project root |
| `1` | Secrets in `.env.example` | Non-empty `KEY=value` line detected |
| `1` | `.env` creation failed | `cp` returned non-zero (e.g., permission denied) |

---

## Standard Output Contract

All messages are written to **stdout**. Each line is prefixed with a status tag.

| Tag | Meaning |
|---|---|
| `[OK]` | Check passed or action completed successfully |
| `[INFO]` | Informational — action taken automatically (e.g., `.gitignore` updated) |
| `[WARNING]` | Non-fatal condition — setup continues |
| `[ERROR]` | Fatal condition — setup stops, non-zero exit follows |

**Success output example** (happy path):

```
[OK]      macOS 14.5 detected.
[OK]      OrbStack is installed.
[OK]      OrbStack is running.
[OK]      Free memory: 8192 MB (threshold: 3000 MB).
[INFO]    Adding .env to .gitignore...
[OK]      .env.example validated — no secrets detected.
[OK]      Created .env from .env.example.

Setup complete. Fill in .env with your credentials, then run:
  docker compose --profile core up -d
```

**Low-memory warning output example**:

```
[OK]      macOS 14.5 detected.
[OK]      OrbStack is installed.
[OK]      OrbStack is running.
[WARNING] Free memory: 1800 MB — below recommended 3000 MB.
          Platform performance may be affected. Consider closing other applications.
[OK]      .env already in .gitignore.
[OK]      .env.example validated — no secrets detected.
[OK]      Created .env from .env.example.

Setup complete. Fill in .env with your credentials, then run:
  docker compose --profile core up -d
```

**OrbStack not installed error example**:

```
[OK]      macOS 14.5 detected.
[ERROR]   OrbStack is not installed.
          Install it with: brew install --cask orbstack
```

**OrbStack not running error example**:

```
[OK]      macOS 14.5 detected.
[OK]      OrbStack is installed.
[ERROR]   OrbStack is installed but not running.
          Open OrbStack.app to start it.
```

**Secrets in `.env.example` error example**:

```
[OK]      macOS 14.5 detected.
[OK]      OrbStack is installed.
[OK]      OrbStack is running.
[OK]      Free memory: 6144 MB (threshold: 3000 MB).
[OK]      .env already in .gitignore.
[ERROR]   .env.example contains non-empty values.
          Remove all secrets from .env.example before committing.
          Offending lines detected — inspect .env.example manually.
```

**`.env` already exists output example**:

```
[OK]      macOS 14.5 detected.
[OK]      OrbStack is installed.
[OK]      OrbStack is running.
[OK]      Free memory: 8192 MB (threshold: 3000 MB).
[OK]      .env already in .gitignore.
[OK]      .env.example validated — no secrets detected.
[INFO]    .env already exists — skipping creation.

Setup complete. .env was not modified.
```

---

## Stderr

Nothing is written to stderr. All output goes to stdout for simpler piping and log capture.

---

## Idempotency Contract

Running `setup-mac.sh` more than once MUST produce identical filesystem state. Specifically:

- If `.env` exists: the file is **not modified**. An `[INFO]` message is printed.
- If `.env` entry already in `.gitignore`: the file is **not modified**. No message printed.
- Exit code on re-run of a fully configured environment: `0`.
