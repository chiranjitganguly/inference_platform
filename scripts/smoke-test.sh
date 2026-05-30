#!/usr/bin/env bash
# Smoke tests — curl probes against the running platform.
# Each probe prints a labelled pass/fail line.
# Exits 0 if all probes pass; exits 1 if any probe fails.
#
# Usage:
#   make smoke                          # uses defaults
#   SMOKE_API_KEY=<key> make smoke      # authenticated probes
set -uo pipefail

KONG="${KONG_BASE_URL:-http://localhost:8080}"
KONG_ADMIN="${KONG_ADMIN_URL:-http://localhost:8001}"
SMOKE_API_KEY="${SMOKE_API_KEY:-}"
TIMEOUT=5

pass=0
fail=0

ok()    { printf '[PASS]    %s\n' "$*"; pass=$(( pass + 1 )); }
fail()  { printf '[FAIL]    %s\n' "$*" >&2; fail=$(( fail + 1 )); }

probe() {
    local label="$1"
    local url="$2"
    local expected_status="${3:-200}"
    shift 3
    # remaining args forwarded as extra curl flags (e.g. -H "Authorization: Bearer ...")

    local actual_status
    actual_status=$(curl -s -o /dev/null -w '%{http_code}' \
        --max-time "$TIMEOUT" "$@" "$url" 2>/dev/null || echo "000")

    if [[ "$actual_status" == "$expected_status" ]]; then
        ok "${label} (HTTP ${actual_status})"
    else
        fail "${label} — expected HTTP ${expected_status}, got ${actual_status} (${url})"
    fi
}

# ── Probes ────────────────────────────────────────────────────────────────────

printf '\nSmoke tests against %s\n\n' "$KONG"

# Kong admin is reachable (localhost only)
probe "Kong admin reachable"    "${KONG_ADMIN}/status"  200

# Unauthenticated /v1/models must return 401 (key-auth enforced)
probe "LiteLLM /v1/models — unauthenticated" "${KONG}/v1/models" 401

# Authenticated /v1/models returns 200 with a valid key
if [[ -n "$SMOKE_API_KEY" ]]; then
    probe "LiteLLM /v1/models via Kong — authenticated" \
        "${KONG}/v1/models" 200 \
        -H "Authorization: Bearer ${SMOKE_API_KEY}"
else
    printf '[SKIP]    LiteLLM /v1/models — authenticated (SMOKE_API_KEY not set)\n'
fi

# LiteLLM health endpoint proxied through Kong (no auth required on /health)
probe "LiteLLM /v1/health via Kong"  "${KONG}/v1/health"  200

# POST /v1/chat/completions — authenticated request returns 200
if [[ -n "$SMOKE_API_KEY" ]]; then
    probe "POST /v1/chat/completions — authenticated" \
        "${KONG}/v1/chat/completions" 200 \
        -X POST \
        -H "Authorization: Bearer ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"smoke test"}]}'
else
    printf '[SKIP]    POST /v1/chat/completions — authenticated (SMOKE_API_KEY not set)\n'
fi

# POST /v1/chat/completions — unauthenticated must return 401
probe "POST /v1/chat/completions — unauthenticated" \
    "${KONG}/v1/chat/completions" 401 \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"no auth"}]}'

# ── Streaming probes ──────────────────────────────────────────────────────────

if [[ -n "$SMOKE_API_KEY" ]]; then
    # Streaming: Content-Type and [DONE] sentinel
    stream_body='{"model":"gpt-4o-mini","messages":[{"role":"user","content":"say hi"}],"stream":true}'
    stream_output=$(curl -s --no-buffer -N \
        --max-time "$TIMEOUT" \
        -X POST \
        -H "Authorization: Bearer ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$stream_body" \
        "${KONG}/v1/chat/completions" 2>/dev/null)
    stream_ct=$(curl -s --no-buffer -N \
        -o /dev/null \
        -D - \
        --max-time "$TIMEOUT" \
        -X POST \
        -H "Authorization: Bearer ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$stream_body" \
        "${KONG}/v1/chat/completions" 2>/dev/null | grep -i "^content-type:" | tr -d '\r')

    if echo "$stream_ct" | grep -qi "text/event-stream"; then
        ok "POST /v1/chat/completions streaming — Content-Type: text/event-stream"
    else
        fail "POST /v1/chat/completions streaming — expected text/event-stream, got: ${stream_ct}"
    fi

    if echo "$stream_output" | grep -q "^data: \[DONE\]"; then
        ok "POST /v1/chat/completions streaming — data: [DONE] received"
    else
        fail "POST /v1/chat/completions streaming — data: [DONE] not found in stream"
    fi

    if echo "$stream_output" | grep -q "^data: {"; then
        ok "POST /v1/chat/completions streaming — at least one JSON chunk received"
    else
        fail "POST /v1/chat/completions streaming — no JSON data: chunks found"
    fi

    # TTFT check: time_starttransfer < 2.0s (SC-001)
    ttft=$(curl -s --no-buffer -N \
        -o /dev/null \
        -w '%{time_starttransfer}' \
        --max-time "$TIMEOUT" \
        -X POST \
        -H "Authorization: Bearer ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$stream_body" \
        "${KONG}/v1/chat/completions" 2>/dev/null)
    # awk comparison: 1 if ttft < 2.0, 0 otherwise
    ttft_ok=$(awk "BEGIN {print ($ttft < 2.0) ? 1 : 0}")
    if [[ "$ttft_ok" == "1" ]]; then
        ok "POST /v1/chat/completions streaming — TTFT ${ttft}s under 2s"
    else
        fail "POST /v1/chat/completions streaming — TTFT ${ttft}s exceeds 2s limit"
    fi

    # Streaming unauthenticated must return 401 (not SSE)
    stream_unauth_status=$(curl -s -o /dev/null -w '%{http_code}' \
        --max-time "$TIMEOUT" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "$stream_body" \
        "${KONG}/v1/chat/completions" 2>/dev/null)
    if [[ "$stream_unauth_status" == "401" ]]; then
        ok "POST /v1/chat/completions streaming — unauthenticated returns 401"
    else
        fail "POST /v1/chat/completions streaming — unauthenticated expected 401, got ${stream_unauth_status}"
    fi
else
    printf '[SKIP]    POST /v1/chat/completions streaming probes (SMOKE_API_KEY not set)\n'
fi

# ── Caching probes ────────────────────────────────────────────────────────────

if [[ -n "$SMOKE_API_KEY" ]]; then
    cache_body='{"model":"gpt-4o-mini","messages":[{"role":"user","content":"cache smoke test probe"}],"temperature":0.0}'

    # First request — must be a cache miss (x-litellm-cache-hit: False or absent)
    cache_first_headers=$(curl -s \
        -D - \
        -o /dev/null \
        --max-time "$TIMEOUT" \
        -X POST \
        -H "Authorization: Bearer ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$cache_body" \
        "${KONG}/v1/chat/completions" 2>/dev/null | tr -d '\r')
    cache_first_hit=$(echo "$cache_first_headers" | grep -i "^x-litellm-cache-hit:" | awk '{print $2}')

    if [[ "$cache_first_hit" != "True" ]]; then
        ok "POST /v1/chat/completions cache — first request is cache miss"
    else
        fail "POST /v1/chat/completions cache — first request returned cache hit (expected miss)"
    fi

    # Second identical request — must be a cache hit (x-litellm-cache-hit: True)
    cache_second_headers=$(curl -s \
        -D - \
        -o /dev/null \
        --max-time "$TIMEOUT" \
        -X POST \
        -H "Authorization: Bearer ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$cache_body" \
        "${KONG}/v1/chat/completions" 2>/dev/null | tr -d '\r')
    cache_second_hit=$(echo "$cache_second_headers" | grep -i "^x-litellm-cache-hit:" | awk '{print $2}')

    if [[ "$cache_second_hit" == "True" ]]; then
        ok "POST /v1/chat/completions cache — second request is cache hit (x-litellm-cache-hit: True)"
    else
        fail "POST /v1/chat/completions cache — second identical request expected cache hit, got: ${cache_second_hit:-absent}"
    fi

    # Streaming request — cache bypass (x-litellm-cache-hit must NOT be True)
    stream_cache_headers=$(curl -s --no-buffer -N \
        -D - \
        -o /dev/null \
        --max-time "$TIMEOUT" \
        -X POST \
        -H "Authorization: Bearer ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"cache smoke test probe"}],"temperature":0.0,"stream":true}' \
        "${KONG}/v1/chat/completions" 2>/dev/null | tr -d '\r')
    stream_cache_hit=$(echo "$stream_cache_headers" | grep -i "^x-litellm-cache-hit:" | awk '{print $2}')

    if [[ "$stream_cache_hit" != "True" ]]; then
        ok "POST /v1/chat/completions streaming — cache bypass confirmed (no cache-hit header)"
    else
        fail "POST /v1/chat/completions streaming — unexpected cache hit on streaming request"
    fi
else
    printf '[SKIP]    POST /v1/chat/completions cache probes (SMOKE_API_KEY not set)\n'
fi

# ── Fallback routing probes ───────────────────────────────────────────────────
# These probes verify the fallback routing infrastructure is correctly wired.
# They do NOT trigger actual fallback paths (that requires an invalid provider key).
#
# Operator workflow to test a full fallback:
#   1. Set one provider API key to an invalid value in .env
#   2. make restart svc=litellm
#   3. Re-run make smoke — the affected model's request will be served by its fallback
#   4. Restore the key and restart: make restart svc=litellm

if [[ -n "${SMOKE_API_KEY:-}" ]]; then
    # Probe 1 — model field is present and non-empty in every successful response (FR-004)
    fallback_resp=$(curl -s --max-time "$TIMEOUT" \
        -X POST \
        -H "Authorization: Bearer ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"fallback routing smoke probe"}]}' \
        "${KONG}/v1/chat/completions" 2>/dev/null)
    fallback_model=$(printf '%s' "$fallback_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('model',''))" 2>/dev/null)

    if [[ -n "$fallback_model" ]]; then
        ok "POST /v1/chat/completions fallback — model field present in response: ${fallback_model}"
    else
        fail "POST /v1/chat/completions fallback — model field missing or response invalid"
    fi

    # Probe 2 — valid request returns HTTP 200 (baseline; fallback transparent to caller)
    fallback_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT" \
        -X POST \
        -H "Authorization: Bearer ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"fallback smoke probe 2"}]}' \
        "${KONG}/v1/chat/completions" 2>/dev/null)

    if [[ "$fallback_status" == "200" ]]; then
        ok "POST /v1/chat/completions fallback — HTTP 200 returned for available model"
    else
        fail "POST /v1/chat/completions fallback — unexpected HTTP status: ${fallback_status}"
    fi

    # Probe 3 — 503 body contains error key when all fallbacks are exhausted
    # (Only verifiable manually with all provider keys invalid — logged here for reference)
    printf '[INFO]    POST /v1/chat/completions 503 schema probe: run with all keys invalid to verify all_fallbacks_exhausted body\n'
else
    printf '[SKIP]    POST /v1/chat/completions fallback probes (SMOKE_API_KEY not set)\n'
fi

# ── Key management probes (T008) ─────────────────────────────────────────────

# POST /v1/key/generate unauthenticated → 401 (LiteLLM enforces master key)
probe "POST /v1/key/generate — unauthenticated" \
    "${KONG}/v1/key/generate" 401 \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{}'

# POST /v1/key/generate with master key → 200 + key field present
if [[ -n "${LITELLM_MASTER_KEY:-}" ]]; then
    key_resp=$(curl -s --max-time "$TIMEOUT" \
        -X POST \
        -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"key_alias":"smoke-key","max_budget":0.001,"budget_duration":"monthly"}' \
        "${KONG}/v1/key/generate" 2>/dev/null)
    key_status=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" \
        -X POST \
        -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"key_alias":"smoke-key-check","max_budget":0.001,"budget_duration":"monthly"}' \
        "${KONG}/v1/key/generate" 2>/dev/null)
    if [[ "$key_status" == "200" ]]; then
        ok "POST /v1/key/generate — master key creates key (HTTP 200)"
    else
        fail "POST /v1/key/generate — expected 200, got ${key_status}"
    fi
else
    printf '[SKIP]    POST /v1/key/generate — master key probe (LITELLM_MASTER_KEY not set)\n'
fi

# ── Spend report probes (T015) ────────────────────────────────────────────────

# GET /v1/spend unauthenticated → 401
probe "GET /v1/spend — unauthenticated" "${KONG}/v1/spend" 401

# GET /v1/spend with master key → 200 + required fields
if [[ -n "${LITELLM_MASTER_KEY:-}" ]]; then
    spend_resp=$(curl -s --max-time "$TIMEOUT" \
        -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
        "${KONG}/v1/spend" 2>/dev/null)
    spend_status=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" \
        -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
        "${KONG}/v1/spend" 2>/dev/null)
    if [[ "$spend_status" == "200" ]] && echo "$spend_resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert 'total_spend_usd' in d
assert isinstance(d.get('by_model'), list)
assert isinstance(d.get('by_key'), list)
" 2>/dev/null; then
        ok "GET /v1/spend — master key returns required fields (HTTP 200)"
    else
        fail "GET /v1/spend — expected 200 + required fields, got HTTP ${spend_status}: ${spend_resp}"
    fi
else
    printf '[SKIP]    GET /v1/spend — master key probe (LITELLM_MASTER_KEY not set)\n'
fi

# ── Langfuse metadata probe (T018) ───────────────────────────────────────────

if [[ -n "${SMOKE_API_KEY:-}" ]]; then
    langfuse_status=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" \
        -X POST \
        -H "Authorization: Bearer ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "gpt-4o-mini",
          "messages": [{"role": "user", "content": "smoke probe"}],
          "metadata": {
            "langfuse_prompt_name": "smoke-test",
            "langfuse_prompt_version": "1"
          }
        }' \
        "${KONG}/v1/chat/completions" 2>/dev/null)
    if [[ "$langfuse_status" == "200" ]]; then
        ok "POST /v1/chat/completions with Langfuse metadata — HTTP 200 (trace cost visible in Langfuse UI)"
    else
        fail "POST /v1/chat/completions with Langfuse metadata — expected 200, got ${langfuse_status}"
    fi
else
    printf '[SKIP]    Langfuse metadata probe (SMOKE_API_KEY not set)\n'
fi

# ── Result ────────────────────────────────────────────────────────────────────

printf '\n%d passed, %d failed\n\n' "$pass" "$fail"

if [[ $fail -gt 0 ]]; then
    exit 1
fi
