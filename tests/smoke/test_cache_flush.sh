#!/usr/bin/env bash
# Smoke tests for DELETE /cache/flush cache management endpoint.
#
# Tests 1–6 from specs/022-cache-flush-management/quickstart.md, run non-interactively.
#
# Prerequisites:
#   make up-core && make up-portal && make seed-kong
#   export SMOKE_API_KEY=<your-master-key>
#
# Usage:
#   bash tests/smoke/test_cache_flush.sh
#   # or via make:
#   make smoke  (this script is sourced by the platform smoke test runner)

set -euo pipefail

GATEWAY_HOST="${GATEWAY_HOST:-localhost}"
GATEWAY_PORT="${GATEWAY_PORT:-8080}"
BASE_URL="http://${GATEWAY_HOST}:${GATEWAY_PORT}"
API_KEY="${SMOKE_API_KEY:-}"
PASS=0
FAIL=0

if [[ -z "$API_KEY" ]]; then
  echo "SKIP: SMOKE_API_KEY not set — skipping cache flush smoke tests"
  exit 0
fi

_pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
_fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

# ── Test 1: Full cache flush returns 200 with keys_deleted ────────────────────
echo "--- Test 1: full cache flush ---"
RESP=$(curl -s -w "\n%{http_code}" -X DELETE "${BASE_URL}/cache/flush" \
  -H "Authorization: Bearer ${API_KEY}")
HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | head -1)

if [[ "$HTTP_CODE" == "200" ]] && echo "$BODY" | grep -q '"keys_deleted"'; then
  _pass "Test 1: full flush returned 200 with keys_deleted"
else
  _fail "Test 1: expected 200 + keys_deleted. Got HTTP $HTTP_CODE, body: ${BODY:0:200}"
fi

# ── Test 2: Model-scoped flush returns 200 with model echo ────────────────────
echo "--- Test 2: model-scoped flush ---"
RESP=$(curl -s -w "\n%{http_code}" -X DELETE "${BASE_URL}/cache/flush?model=gpt-4o-mini" \
  -H "Authorization: Bearer ${API_KEY}")
HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | head -1)

if [[ "$HTTP_CODE" == "200" ]] && \
   echo "$BODY" | grep -q '"keys_deleted"' && \
   echo "$BODY" | grep -q '"model".*"gpt-4o-mini"'; then
  _pass "Test 2: scoped flush returned 200 with keys_deleted and model echo"
else
  _fail "Test 2: expected 200 + keys_deleted + model echo. Got HTTP $HTTP_CODE, body: ${BODY:0:200}"
fi

# ── Test 3: No API key returns 401 ───────────────────────────────────────────
echo "--- Test 3: missing key returns 401 ---"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "${BASE_URL}/cache/flush")

if [[ "$HTTP_CODE" == "401" ]]; then
  _pass "Test 3: missing key returned 401"
else
  _fail "Test 3: expected 401. Got HTTP $HTTP_CODE"
fi

# ── Test 4: Non-master key returns 403 ───────────────────────────────────────
echo "--- Test 4: non-master key returns 403 ---"
RESP=$(curl -s -w "\n%{http_code}" -X DELETE "${BASE_URL}/cache/flush" \
  -H "Authorization: Bearer sk-non-master-consumer-key-placeholder")
HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | head -1)

if [[ "$HTTP_CODE" == "403" ]] && echo "$BODY" | grep -q '"error".*"forbidden"'; then
  _pass "Test 4: non-master key returned 403 forbidden"
else
  _fail "Test 4: expected 403 forbidden. Got HTTP $HTTP_CODE, body: ${BODY:0:200}"
fi

# ── Test 5: Invalid model name returns 422 with valid_models list ─────────────
echo "--- Test 5: invalid model returns 422 ---"
RESP=$(curl -s -w "\n%{http_code}" -X DELETE \
  "${BASE_URL}/cache/flush?model=nonexistent-model-xyz-99999" \
  -H "Authorization: Bearer ${API_KEY}")
HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | head -1)

if [[ "$HTTP_CODE" == "422" ]] && \
   echo "$BODY" | grep -q '"error".*"invalid_model"' && \
   echo "$BODY" | grep -q '"valid_models"'; then
  _pass "Test 5: invalid model returned 422 with valid_models list"
else
  _fail "Test 5: expected 422 + invalid_model + valid_models. Got HTTP $HTTP_CODE, body: ${BODY:0:300}"
fi

# ── Test 6: Idempotency — flush again returns keys_deleted=0 ─────────────────
echo "--- Test 6: idempotency (double flush returns 0) ---"
# First flush to ensure cache is empty
curl -s -o /dev/null -X DELETE "${BASE_URL}/cache/flush" -H "Authorization: Bearer ${API_KEY}"

# Second flush should return 0
RESP=$(curl -s -w "\n%{http_code}" -X DELETE "${BASE_URL}/cache/flush" \
  -H "Authorization: Bearer ${API_KEY}")
HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | head -1)

if [[ "$HTTP_CODE" == "200" ]] && echo "$BODY" | grep -q '"keys_deleted":0'; then
  _pass "Test 6: second flush returned 200 with keys_deleted=0 (idempotent)"
else
  _fail "Test 6: expected 200 + keys_deleted=0. Got HTTP $HTTP_CODE, body: ${BODY:0:200}"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Cache flush smoke tests: ${PASS} passed, ${FAIL} failed"
[[ $FAIL -eq 0 ]]
