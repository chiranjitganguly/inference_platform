#!/usr/bin/env bash
# scripts/setup-mac.sh — first-time developer environment setup for the AI Inference Platform
# Requirements: macOS 14+, OrbStack installed and running
# Usage: bash scripts/setup-mac.sh
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────

ok()   { printf '[OK]      %s\n' "$*"; }
info() { printf '[INFO]    %s\n' "$*"; }
warn() { printf '[WARNING] %s\n' "$*"; }
err()  { printf '[ERROR]   %s\n' "$*"; }

# ─────────────────────────────────────────────────────────────────────────────
# Platform guard
# ─────────────────────────────────────────────────────────────────────────────

check_platform() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    err "This script requires macOS."
    exit 1
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# macOS version gate — 14.0+ required
# ─────────────────────────────────────────────────────────────────────────────

check_macos_version() {
  local os_version os_major
  os_version=$(sw_vers -productVersion)
  os_major=$(echo "$os_version" | cut -d. -f1)
  if [[ "$os_major" -lt 14 ]]; then
    err "macOS 14.0+ required. Detected: $os_version"
    exit 1
  fi
  ok "macOS $os_version detected."
}

# ─────────────────────────────────────────────────────────────────────────────
# OrbStack checks
# ─────────────────────────────────────────────────────────────────────────────

check_orbstack_installed() {
  if command -v orbctl &>/dev/null; then
    ok "OrbStack is installed."
  else
    err "OrbStack is not installed."
    printf '        Install with: brew install --cask orbstack\n'
    exit 1
  fi
}

check_orbstack_running() {
  if orbctl status &>/dev/null 2>&1; then
    ok "OrbStack is running."
  else
    err "OrbStack is installed but not running."
    printf '        Open OrbStack.app to start it.\n'
    exit 1
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Memory check — 3000 MB threshold via vm_stat
# Warning only: setup continues even when below threshold
# ─────────────────────────────────────────────────────────────────────────────

check_memory() {
  local page_size free_pages free_mb
  page_size=$(sysctl -n hw.pagesize 2>/dev/null || echo 16384)
  free_pages=$(vm_stat | awk '/^Pages free:/ { gsub(/\.$/, "", $3); print $3 }')
  free_mb=$(( free_pages * page_size / 1048576 ))

  if [[ $free_mb -ge 3000 ]]; then
    ok "Free memory: ${free_mb} MB (threshold: 3000 MB)."
  else
    warn "Free memory: ${free_mb} MB — below recommended 3000 MB."
    printf '          Consider closing other applications before starting the platform.\n'
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# .env.example validation
# Fails if file is missing or contains any non-empty variable values
# ─────────────────────────────────────────────────────────────────────────────

validate_env_example() {
  if [[ ! -f .env.example ]]; then
    err ".env.example not found at project root."
    exit 1
  fi
  if grep -vE '^\s*#|^\s*$' .env.example | grep -qE '^[A-Za-z_][A-Za-z0-9_]*=.+'; then
    err ".env.example contains non-empty values — remove all secrets before committing."
    exit 1
  fi
  ok ".env.example validated — no secrets detected."
}

# ─────────────────────────────────────────────────────────────────────────────
# .gitignore guard — ensures .env is never committed
# ─────────────────────────────────────────────────────────────────────────────

guard_gitignore() {
  if grep -qxF '.env' .gitignore 2>/dev/null; then
    ok ".env already in .gitignore."
  else
    info "Adding .env to .gitignore..."
    echo ".env" >> .gitignore
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# .env creation — never overwrites an existing file
# ─────────────────────────────────────────────────────────────────────────────

create_env_file() {
  if [[ -f .env ]]; then
    info ".env already exists — skipping creation."
  else
    if ! cp .env.example .env; then
      err "Failed to create .env (check directory permissions)."
      exit 1
    fi
    ok "Created .env from .env.example."
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

main() {
  check_platform
  check_macos_version
  check_orbstack_installed
  check_orbstack_running
  check_memory
  validate_env_example
  guard_gitignore
  create_env_file

  printf '\nSetup complete. Fill in .env with your credentials, then run:\n'
  printf '  make up-core\n'
}

main "$@"
