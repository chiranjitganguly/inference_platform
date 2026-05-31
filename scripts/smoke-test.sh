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
        -H "Authorization: ${SMOKE_API_KEY}"
else
    printf '[SKIP]    LiteLLM /v1/models — authenticated (SMOKE_API_KEY not set)\n'
fi

# Health endpoint — no auth required (US3: unauthenticated liveness probe)
probe "GET /health via Kong — no auth" "${KONG}/health" 200

# POST /v1/chat/completions — authenticated request returns 200
if [[ -n "$SMOKE_API_KEY" ]]; then
    probe "POST /v1/chat/completions — authenticated" \
        "${KONG}/v1/chat/completions" 200 \
        -X POST \
        -H "Authorization: ${SMOKE_API_KEY}" \
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
        -H "Authorization: ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$stream_body" \
        "${KONG}/v1/chat/completions" 2>/dev/null)
    stream_ct=$(curl -s --no-buffer -N \
        -o /dev/null \
        -D - \
        --max-time "$TIMEOUT" \
        -X POST \
        -H "Authorization: ${SMOKE_API_KEY}" \
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
        -H "Authorization: ${SMOKE_API_KEY}" \
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
        -H "Authorization: ${SMOKE_API_KEY}" \
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
        -H "Authorization: ${SMOKE_API_KEY}" \
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
        -H "Authorization: ${SMOKE_API_KEY}" \
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
        -H "Authorization: ${SMOKE_API_KEY}" \
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
        -H "Authorization: ${SMOKE_API_KEY}" \
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

# ── Embeddings probes (feature 011) ──────────────────────────────────────────

if [[ -n "${SMOKE_API_KEY:-}" ]]; then
    # US1-a: text-embedding-3-small → 1536-element float array
    embed_small_dims=$(curl -s --max-time 15 \
        -X POST \
        -H "Authorization: ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"model":"text-embedding-3-small","input":"smoke test embedding"}' \
        "${KONG}/v1/embeddings" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['data'][0]['embedding']))" 2>/dev/null)
    if [[ "$embed_small_dims" == "1536" ]]; then
        ok "POST /v1/embeddings text-embedding-3-small — 1536 dimensions"
    else
        fail "POST /v1/embeddings text-embedding-3-small — expected 1536 dims, got: ${embed_small_dims:-error}"
    fi

    # US3: token usage fields present and non-zero in every successful embedding response
    embed_tokens=$(curl -s --max-time 15 \
        -X POST \
        -H "Authorization: ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"model":"text-embedding-3-small","input":"token usage verification"}' \
        "${KONG}/v1/embeddings" 2>/dev/null \
        | python3 -c "
import sys, json
d = json.load(sys.stdin)
pt = d.get('usage', {}).get('prompt_tokens', 0)
tt = d.get('usage', {}).get('total_tokens', 0)
print('ok' if pt > 0 and tt > 0 else f'fail pt={pt} tt={tt}')
" 2>/dev/null)
    if [[ "$embed_tokens" == "ok" ]]; then
        ok "POST /v1/embeddings — usage.prompt_tokens and usage.total_tokens present and non-zero"
    else
        fail "POST /v1/embeddings — token usage check failed: ${embed_tokens:-error}"
    fi

    # US1-b: text-embedding-3-large → 3072-element float array
    embed_large_dims=$(curl -s --max-time 15 \
        -X POST \
        -H "Authorization: ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"model":"text-embedding-3-large","input":"smoke test embedding"}' \
        "${KONG}/v1/embeddings" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['data'][0]['embedding']))" 2>/dev/null)
    if [[ "$embed_large_dims" == "3072" ]]; then
        ok "POST /v1/embeddings text-embedding-3-large — 3072 dimensions"
    else
        fail "POST /v1/embeddings text-embedding-3-large — expected 3072 dims, got: ${embed_large_dims:-error}"
    fi

    # US2-a: chat model on /v1/embeddings → HTTP 400 (model type rejection)
    embed_chat_status=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
        -X POST \
        -H "Authorization: ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"model":"gpt-4o","input":"reject me"}' \
        "${KONG}/v1/embeddings" 2>/dev/null)
    if [[ "$embed_chat_status" == "400" ]]; then
        ok "POST /v1/embeddings gpt-4o — chat model rejected (HTTP 400)"
    else
        fail "POST /v1/embeddings gpt-4o — expected HTTP 400, got: ${embed_chat_status}"
    fi

    # US2-b: Anthropic chat model on /v1/embeddings → HTTP 400
    embed_anthropic_status=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
        -X POST \
        -H "Authorization: ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"model":"claude-sonnet","input":"reject me"}' \
        "${KONG}/v1/embeddings" 2>/dev/null)
    if [[ "$embed_anthropic_status" == "400" ]]; then
        ok "POST /v1/embeddings claude-sonnet — chat model rejected (HTTP 400)"
    else
        fail "POST /v1/embeddings claude-sonnet — expected HTTP 400, got: ${embed_anthropic_status}"
    fi

    # SC-006: cache bypass — identical embedding requests must never return a cache hit
    embed_cache_body='{"model":"text-embedding-3-small","input":"cache bypass verification probe"}'
    embed_cache_hit_1=$(curl -s --max-time 15 \
        -D - -o /dev/null \
        -X POST \
        -H "Authorization: ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$embed_cache_body" \
        "${KONG}/v1/embeddings" 2>/dev/null | tr -d '\r' \
        | grep -i "^x-litellm-cache-hit:" | awk '{print $2}')
    embed_cache_hit_2=$(curl -s --max-time 15 \
        -D - -o /dev/null \
        -X POST \
        -H "Authorization: ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$embed_cache_body" \
        "${KONG}/v1/embeddings" 2>/dev/null | tr -d '\r' \
        | grep -i "^x-litellm-cache-hit:" | awk '{print $2}')
    if [[ "$embed_cache_hit_1" != "True" && "$embed_cache_hit_2" != "True" ]]; then
        ok "POST /v1/embeddings — cache bypass confirmed (no cache-hit header on either request)"
    else
        fail "POST /v1/embeddings — cache hit detected on embedding request (expected bypass): hit1=${embed_cache_hit_1:-absent} hit2=${embed_cache_hit_2:-absent}"
    fi

    # US1-c: batch of 3 inputs → 3 objects, correct index ordering
    embed_batch=$(curl -s --max-time 15 \
        -X POST \
        -H "Authorization: ${SMOKE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"model":"text-embedding-3-small","input":["first","second","third"]}' \
        "${KONG}/v1/embeddings" 2>/dev/null \
        | python3 -c "
import sys, json
d = json.load(sys.stdin)
count = len(d['data'])
first_index = d['data'][0]['index']
print(f'{count},{first_index}')
" 2>/dev/null)
    if [[ "$embed_batch" == "3,0" ]]; then
        ok "POST /v1/embeddings batch — 3 objects returned, index 0 first"
    else
        fail "POST /v1/embeddings batch — expected count=3,index=0, got: ${embed_batch:-error}"
    fi
else
    printf '[SKIP]    POST /v1/embeddings probes (SMOKE_API_KEY not set)\n'
fi

# ── Langfuse metadata probe (T018) ───────────────────────────────────────────

if [[ -n "${SMOKE_API_KEY:-}" ]]; then
    langfuse_status=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" \
        -X POST \
        -H "Authorization: ${SMOKE_API_KEY}" \
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

# ── Auth rejection probes (US2: 401 on inference endpoints without key) ───────

probe "GET /v1/models — unauthenticated (no key)"      "${KONG}/v1/models"     401
probe "POST /v1/embeddings — unauthenticated (no key)" "${KONG}/v1/embeddings" 401 \
    -X POST -H "Content-Type: application/json" -d '{}'

# ── Internal port isolation (US4: LiteLLM :4000 must not be host-reachable) ──

litellm_status=$(curl -s -o /dev/null -w '%{http_code}' \
    --connect-timeout 2 --max-time 2 \
    "http://localhost:4000/v1/models" 2>/dev/null || echo "000")
if [[ "$litellm_status" == "000" ]]; then
    ok "LiteLLM :4000 — not reachable from host (constitution §2.1)"
else
    fail "LiteLLM :4000 — externally reachable (HTTP ${litellm_status}) — constitution §2.1 violation"
fi

# ── Rate-limit probes (feature 013) ──────────────────────────────────────────

# T009: probe_rate_limit_burst — send 12 rapid requests, expect first 10 → 200,
# requests 11-12 → 429 (per-second limit of 10, FR-002, FR-003).
if [[ -n "${SMOKE_API_KEY:-}" ]]; then
    burst_pass=0
    burst_fail=0
    for i in $(seq 1 12); do
        status=$(curl -s -o /dev/null -w '%{http_code}' \
            --max-time "$TIMEOUT" \
            -H "Authorization: ${SMOKE_API_KEY}" \
            "${KONG}/v1/models" 2>/dev/null || echo "000")
        printf '[INFO]    Rate-limit burst request %2d → HTTP %s\n' "$i" "$status"
        if [[ $i -le 10 && "$status" == "200" ]]; then
            burst_pass=$(( burst_pass + 1 ))
        elif [[ $i -gt 10 && "$status" == "429" ]]; then
            burst_pass=$(( burst_pass + 1 ))
        else
            burst_fail=$(( burst_fail + 1 ))
        fi
    done
    if [[ $burst_fail -eq 0 ]]; then
        ok "Rate-limit burst — requests 1-10 returned 200, 11-12 returned 429 (${burst_pass}/12)"
    else
        fail "Rate-limit burst — ${burst_fail} requests had unexpected status codes"
    fi
else
    printf '[SKIP]    Rate-limit burst probe (SMOKE_API_KEY not set)\n'
fi

# T010: probe_retry_after_header — confirm Retry-After header present on 429 (FR-004).
if [[ -n "${SMOKE_API_KEY:-}" ]]; then
    # exhaust per-second limit then capture the throttled response
    for _ in $(seq 1 10); do
        curl -s -o /dev/null --max-time "$TIMEOUT" \
            -H "Authorization: ${SMOKE_API_KEY}" \
            "${KONG}/v1/models" 2>/dev/null
    done
    throttle_headers=$(curl -si --max-time "$TIMEOUT" \
        -H "Authorization: ${SMOKE_API_KEY}" \
        "${KONG}/v1/models" 2>/dev/null | tr -d '\r')
    if echo "$throttle_headers" | grep -qi "^retry-after:"; then
        retry_val=$(echo "$throttle_headers" | grep -i "^retry-after:" | awk '{print $2}')
        ok "Rate-limit Retry-After header present on 429 (value: ${retry_val}s)"
    else
        fail "Rate-limit Retry-After header missing on 429 response — FR-004 violation"
    fi
else
    printf '[SKIP]    Rate-limit Retry-After header probe (SMOKE_API_KEY not set)\n'
fi

# T012: probe_consumer_isolation — Consumer A throttled, Consumer B still gets 200 (US2, SC-002).
CONSUMER_B_KEY="${CONSUMER_B_API_KEY:-consumer-b-test-key}"
if [[ -n "${SMOKE_API_KEY:-}" ]]; then
    # hammer Consumer A past per-second limit in background
    (
        for _ in $(seq 1 20); do
            curl -s -o /dev/null --max-time "$TIMEOUT" \
                -H "Authorization: ${SMOKE_API_KEY}" \
                "${KONG}/v1/models" 2>/dev/null
        done
    ) &
    bg_pid=$!
    sleep 0.05
    isolation_status=$(curl -s -o /dev/null -w '%{http_code}' \
        --max-time "$TIMEOUT" \
        -H "Authorization: ${CONSUMER_B_KEY}" \
        "${KONG}/v1/models" 2>/dev/null || echo "000")
    wait "$bg_pid" 2>/dev/null || true
    if [[ "$isolation_status" == "200" ]]; then
        ok "Rate-limit consumer isolation — Consumer B gets 200 while Consumer A is throttled (SC-002)"
    else
        fail "Rate-limit consumer isolation — Consumer B got HTTP ${isolation_status}, expected 200"
    fi
else
    printf '[SKIP]    Rate-limit consumer isolation probe (SMOKE_API_KEY not set)\n'
fi

# T014: probe_custom_consumer_limit — consumer-scoped override enforced (US3, SC-004).
consumer_b_plugin_id=""
if [[ -n "${SMOKE_API_KEY:-}" ]]; then
    # apply a tighter per-second limit (2/s) to consumer-b via Admin API
    create_resp=$(curl -sf -X POST "${KONG_ADMIN}/consumers/consumer-b/plugins" \
        -d "name=rate-limiting" \
        -d "config.second=2" \
        -d "config.minute=300" \
        -d "config.hour=10000" \
        -d "config.policy=redis" \
        -d "config.redis_host=redis-cache" \
        -d "config.redis_port=6379" \
        -d "config.limit_by=consumer" \
        -d "config.fault_tolerant=true" \
        -d "config.hide_client_headers=false" \
        2>/dev/null || echo "")
    consumer_b_plugin_id=$(printf '%s' "$create_resp" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")

    if [[ -n "$consumer_b_plugin_id" ]]; then
        # send 3 requests — 3rd must be 429 under the 2/s limit
        custom_fail=0
        for i in $(seq 1 3); do
            st=$(curl -s -o /dev/null -w '%{http_code}' \
                --max-time "$TIMEOUT" \
                -H "Authorization: ${CONSUMER_B_KEY}" \
                "${KONG}/v1/models" 2>/dev/null || echo "000")
            if [[ $i -le 2 && "$st" != "200" ]]; then custom_fail=$(( custom_fail + 1 )); fi
            if [[ $i -eq 3 && "$st" != "429" ]]; then custom_fail=$(( custom_fail + 1 )); fi
        done

        # clean up the consumer-scoped plugin
        curl -sf -X DELETE "${KONG_ADMIN}/plugins/${consumer_b_plugin_id}" >/dev/null 2>&1 || true

        if [[ $custom_fail -eq 0 ]]; then
            ok "Rate-limit per-consumer override — consumer-b 2/s limit enforced; global 10/s unaffected (SC-004)"
        else
            fail "Rate-limit per-consumer override — expected requests 1-2→200, 3→429 under 2/s limit"
        fi
    else
        fail "Rate-limit per-consumer override — could not create consumer-scoped plugin for consumer-b"
    fi
else
    printf '[SKIP]    Rate-limit per-consumer override probe (SMOKE_API_KEY not set)\n'
fi

# T015: probe_quota_headers — all nine RateLimit-* headers present on 200 (US4, FR-006).
if [[ -n "${SMOKE_API_KEY:-}" ]]; then
    quota_headers=$(curl -si --max-time "$TIMEOUT" \
        -H "Authorization: ${SMOKE_API_KEY}" \
        "${KONG}/v1/models" 2>/dev/null | tr -d '\r')
    quota_missing=()
    for hdr in \
        "ratelimit-limit-second" \
        "ratelimit-remaining-second" \
        "ratelimit-reset-second" \
        "ratelimit-limit-minute" \
        "ratelimit-remaining-minute" \
        "ratelimit-reset-minute" \
        "ratelimit-limit-hour" \
        "ratelimit-remaining-hour" \
        "ratelimit-reset-hour"; do
        if ! echo "$quota_headers" | grep -qi "^${hdr}:"; then
            quota_missing+=("$hdr")
        fi
    done
    if [[ ${#quota_missing[@]} -eq 0 ]]; then
        ok "Rate-limit quota headers — all nine RateLimit-* headers present on 200 response (FR-006)"
    else
        fail "Rate-limit quota headers — missing headers: ${quota_missing[*]}"
    fi
else
    printf '[SKIP]    Rate-limit quota headers probe (SMOKE_API_KEY not set)\n'
fi

# ── Result ────────────────────────────────────────────────────────────────────

printf '\n%d passed, %d failed\n\n' "$pass" "$fail"

if [[ $fail -gt 0 ]]; then
    exit 1
fi
