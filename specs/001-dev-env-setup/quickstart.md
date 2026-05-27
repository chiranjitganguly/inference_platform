# Quickstart: Developer Environment Setup

**Feature**: `001-dev-env-setup`
**Updated**: 2026-05-26

---

## Prerequisites

Before running setup, ensure the following are true:

| Requirement | How to check | Fix if missing |
|---|---|---|
| macOS 14.0+ | `sw_vers -productVersion` | Upgrade macOS |
| OrbStack installed | `command -v orbctl` | `brew install --cask orbstack` |
| OrbStack running | `orbctl status` | Open OrbStack.app |
| Project root | `ls .env.example` | `cd` into the repo root |

---

## Step 1 — Run the Setup Script

From the project root:

```bash
bash scripts/setup-mac.sh
```

The script will:

1. Verify macOS 14+ ✓
2. Verify OrbStack is installed ✓
3. Verify OrbStack is running ✓
4. Check free memory (warns if < 3000 MB, does not block) ✓
5. Ensure `.env` is listed in `.gitignore` ✓
6. Validate `.env.example` contains no secrets ✓
7. Copy `.env.example` → `.env` (skipped if `.env` already exists) ✓

**Expected output on success:**

```
[OK]      macOS 14.x detected.
[OK]      OrbStack is installed.
[OK]      OrbStack is running.
[OK]      Free memory: XXXX MB (threshold: 3000 MB).
[OK]      .env already in .gitignore.   (or [INFO] Adding .env to .gitignore...)
[OK]      .env.example validated — no secrets detected.
[OK]      Created .env from .env.example.

Setup complete. Fill in .env with your credentials, then run:
  docker compose --profile core up -d
```

---

## Step 2 — Fill in `.env`

The created `.env` file has all variables with empty values. Open it and fill in the
credentials for your environment:

```bash
# Open in your editor of choice
code .env
# or
nano .env
```

> **Never commit `.env`.** It is gitignored and contains real credentials.
> See `.env.example` for the list of required variable names.

---

## Step 3 — Start the Platform

Once `.env` is populated, start the core profile:

```bash
docker compose --profile core up -d
```

---

## Troubleshooting

### `[ERROR] OrbStack is not installed`

```bash
brew install --cask orbstack
# Then open OrbStack.app and run setup again
```

### `[ERROR] OrbStack is installed but not running`

Open `OrbStack.app` from your Applications folder, wait for the menu bar icon to appear,
then run the setup script again.

### `[WARNING] Free memory: XXXX MB — below recommended 3000 MB`

This is a non-fatal warning. Setup completes normally. For best performance, close other
memory-heavy applications before starting the platform (e.g., Slack, Chrome, Xcode).

### `[ERROR] .env.example contains non-empty values`

A contributor accidentally committed a secret value into `.env.example`. Do not proceed.
Contact the team to remove the secret and rotate it before re-running setup.

### `[INFO] .env already exists — skipping creation`

Your `.env` is already configured. No action needed. If you want to reset it:

```bash
rm .env && bash scripts/setup-mac.sh
```

### `[ERROR] Failed to create .env (check directory permissions)`

```bash
ls -la .        # Check write permissions on project root
chmod u+w .     # Grant write permission if needed
```

---

## Re-running Setup

The script is safe to run multiple times. It will skip any step that is already complete
and report `[INFO]` messages for skipped actions.
