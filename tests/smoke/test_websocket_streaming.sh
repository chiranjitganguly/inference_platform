#!/usr/bin/env bash
# Smoke tests for /ws/v1/chat/completions WebSocket streaming endpoint.
#
# Tests 1–4 from specs/021-websocket-streaming/quickstart.md, run non-interactively.
#
# Prerequisites:
#   npm install -g wscat
#   make up-core && make seed-kong
#   export SMOKE_API_KEY=<your-key>
#
# Usage:
#   bash tests/smoke/test_websocket_streaming.sh
#   # or via make:
#   make smoke  (this script is sourced by the platform smoke test runner)

set -euo pipefail

GATEWAY_HOST="${GATEWAY_HOST:-localhost}"
GATEWAY_PORT="${GATEWAY_PORT:-8080}"
WS_URL="ws://${GATEWAY_HOST}:${GATEWAY_PORT}/ws/v1/chat/completions"
API_KEY="${SMOKE_API_KEY:-}"
PASS=0
FAIL=0

if [[ -z "$API_KEY" ]]; then
  echo "SKIP: SMOKE_API_KEY not set — skipping WebSocket smoke tests"
  exit 0
fi

command -v wscat >/dev/null 2>&1 || {
  echo "SKIP: wscat not installed (npm install -g wscat) — skipping WebSocket smoke tests"
  exit 0
}

_pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
_fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

# ── Test 1: Basic token stream ────────────────────────────────────────────────
echo "--- Test 1: basic token stream ---"
OUTPUT=$(echo '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say OK in one word."}]}' \
  | timeout 30 wscat \
      --connect "$WS_URL" \
      --header "Authorization: ${API_KEY}" \
      --execute - 2>&1 || true)

if echo "$OUTPUT" | grep -q '"object":"chat.completion.chunk"' && \
   echo "$OUTPUT" | grep -q '"type":"stream_complete"'; then
  _pass "Test 1: received delta frames and stream_complete"
else
  _fail "Test 1: expected chat.completion.chunk frames and stream_complete. Got: ${OUTPUT:0:300}"
fi

# ── Test 2: Unauthenticated connection rejected ───────────────────────────────
echo "--- Test 2: unauthenticated rejection ---"
AUTH_OUTPUT=$(timeout 5 wscat --connect "$WS_URL" 2>&1 || true)
if echo "$AUTH_OUTPUT" | grep -qiE "401|unauthorized|unexpected server response"; then
  _pass "Test 2: unauthenticated connection rejected"
else
  _fail "Test 2: expected 401 rejection. Got: ${AUTH_OUTPUT:0:200}"
fi

# ── Test 3: Malformed payload returns validation_error ────────────────────────
echo "--- Test 3: malformed payload ---"
INVALID_OUTPUT=$(echo 'not valid json' \
  | timeout 15 wscat \
      --connect "$WS_URL" \
      --header "Authorization: ${API_KEY}" \
      --execute - 2>&1 || true)

if echo "$INVALID_OUTPUT" | grep -q '"type":"validation_error"'; then
  _pass "Test 3: malformed payload returned validation_error"
else
  _fail "Test 3: expected validation_error. Got: ${INVALID_OUTPUT:0:300}"
fi

# ── Test 4: Missing required fields returns validation_error ──────────────────
echo "--- Test 4: missing required fields ---"
MISSING_OUTPUT=$(echo '{"model":"gpt-4o-mini"}' \
  | timeout 15 wscat \
      --connect "$WS_URL" \
      --header "Authorization: ${API_KEY}" \
      --execute - 2>&1 || true)

if echo "$MISSING_OUTPUT" | grep -q '"type":"validation_error"'; then
  _pass "Test 4: missing fields returned validation_error"
else
  _fail "Test 4: expected validation_error. Got: ${MISSING_OUTPUT:0:300}"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "WebSocket smoke tests: ${PASS} passed, ${FAIL} failed"
[[ $FAIL -eq 0 ]]
