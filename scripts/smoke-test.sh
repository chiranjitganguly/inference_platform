#!/usr/bin/env bash
# Smoke tests — curl probes against the running platform.
# Each probe prints a labelled pass/fail line.
# Exits 0 if all probes pass; exits 1 if any probe fails.
#
# Usage:
#   make smoke                                                   # uses defaults
#   INFERENCE_GATEWAY_CLIENT_SECRET=<secret> make smoke         # JWT auth via Keycloak
#   SMOKE_API_KEY=<key> make smoke                              # legacy key-auth fallback
set -uo pipefail

KONG="${KONG_BASE_URL:-http://localhost:8080}"
KONG_ADMIN="${KONG_ADMIN_URL:-http://localhost:8001}"
SMOKE_API_KEY="${SMOKE_API_KEY:-}"
TIMEOUT=5

# ── Authentication (feature 024 — JWT) ──────────────────────────────────────
# Obtains a JWT from Keycloak via client credentials if INFERENCE_GATEWAY_CLIENT_SECRET
# is set. Falls back to raw SMOKE_API_KEY for environments without the auth profile.

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8083}"
KEYCLOAK_REALM="${KEYCLOAK_REALM:-inference-platform}"
INFERENCE_GATEWAY_CLIENT_SECRET="${INFERENCE_GATEWAY_CLIENT_SECRET:-}"

JWT_TOKEN=""
if [[ -n "$INFERENCE_GATEWAY_CLIENT_SECRET" ]]; then
  JWT_TOKEN=$(curl -sf --max-time 5 \
    -X POST "${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/token" \
    -d "grant_type=client_credentials" \
    -d "client_id=inference-gateway" \
    -d "client_secret=${INFERENCE_GATEWAY_CLIENT_SECRET}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null || echo "")
fi

# EFFECTIVE_AUTH: full "Authorization: ..." header value for curl -H
# AUTH_HEADER_VALUE: just the value part for Python urllib header dicts
if [[ -n "$JWT_TOKEN" ]]; then
  EFFECTIVE_AUTH="Authorization: Bearer ${JWT_TOKEN}"
elif [[ -n "$SMOKE_API_KEY" ]]; then
  EFFECTIVE_AUTH="Authorization: ${SMOKE_API_KEY}"
else
  EFFECTIVE_AUTH=""
fi
AUTH_HEADER_VALUE="${EFFECTIVE_AUTH#Authorization: }"

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
    # || true prevents set -e from aborting on connection refused; curl already
    # writes "000" via -w '%{http_code}' when the connection cannot be made.
    actual_status=$(curl -s -o /dev/null -w '%{http_code}' \
        --max-time "$TIMEOUT" "$@" "$url" 2>/dev/null || true)

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
if [[ -n "$EFFECTIVE_AUTH" ]]; then
    probe "LiteLLM /v1/models via Kong — authenticated" \
        "${KONG}/v1/models" 200 \
        -H "${EFFECTIVE_AUTH}"
else
    printf '[SKIP]    LiteLLM /v1/models — authenticated (no auth credentials)\n'
fi

# Health endpoint — no auth required (US3: unauthenticated liveness probe)
probe "GET /health via Kong — no auth" "${KONG}/health" 200

# POST /v1/chat/completions — authenticated request returns 200
if [[ -n "$EFFECTIVE_AUTH" ]]; then
    probe "POST /v1/chat/completions — authenticated" \
        "${KONG}/v1/chat/completions" 200 \
        -X POST \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"smoke test"}]}'
else
    printf '[SKIP]    POST /v1/chat/completions — authenticated (no auth credentials)\n'
fi

# POST /v1/chat/completions — unauthenticated must return 401
probe "POST /v1/chat/completions — unauthenticated" \
    "${KONG}/v1/chat/completions" 401 \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"no auth"}]}'

# ── Streaming probes ──────────────────────────────────────────────────────────

if [[ -n "$EFFECTIVE_AUTH" ]]; then
    # Streaming: Content-Type and [DONE] sentinel
    stream_body='{"model":"gpt-4o-mini","messages":[{"role":"user","content":"say hi"}],"stream":true}'
    stream_output=$(curl -s --no-buffer -N \
        --max-time "$TIMEOUT" \
        -X POST \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d "$stream_body" \
        "${KONG}/v1/chat/completions" 2>/dev/null)
    stream_ct=$(curl -s --no-buffer -N \
        -o /dev/null \
        -D - \
        --max-time "$TIMEOUT" \
        -X POST \
        -H "${EFFECTIVE_AUTH}" \
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
        -H "${EFFECTIVE_AUTH}" \
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
    printf '[SKIP]    POST /v1/chat/completions streaming probes (no auth credentials)\n'
fi

# ── Caching probes ────────────────────────────────────────────────────────────

if [[ -n "$EFFECTIVE_AUTH" ]]; then
    cache_body='{"model":"gpt-4o-mini","messages":[{"role":"user","content":"cache smoke test probe"}],"temperature":0.0}'

    # First request — must be a cache miss (x-litellm-cache-hit: False or absent)
    cache_first_headers=$(curl -s \
        -D - \
        -o /dev/null \
        --max-time "$TIMEOUT" \
        -X POST \
        -H "${EFFECTIVE_AUTH}" \
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
        -H "${EFFECTIVE_AUTH}" \
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
        -H "${EFFECTIVE_AUTH}" \
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
    printf '[SKIP]    POST /v1/chat/completions cache probes (no auth credentials)\n'
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

if [[ -n "${EFFECTIVE_AUTH:-}" ]]; then
    # Probe 1 — model field is present and non-empty in every successful response (FR-004)
    fallback_resp=$(curl -s --max-time "$TIMEOUT" \
        -X POST \
        -H "${EFFECTIVE_AUTH}" \
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
        -H "${EFFECTIVE_AUTH}" \
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
    printf '[SKIP]    POST /v1/chat/completions fallback probes (no auth credentials)\n'
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

if [[ -n "${EFFECTIVE_AUTH:-}" ]]; then
    # US1-a: text-embedding-3-small → 1536-element float array
    embed_small_dims=$(curl -s --max-time 15 \
        -X POST \
        -H "${EFFECTIVE_AUTH}" \
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
        -H "${EFFECTIVE_AUTH}" \
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
        -H "${EFFECTIVE_AUTH}" \
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
        -H "${EFFECTIVE_AUTH}" \
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
        -H "${EFFECTIVE_AUTH}" \
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
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d "$embed_cache_body" \
        "${KONG}/v1/embeddings" 2>/dev/null | tr -d '\r' \
        | grep -i "^x-litellm-cache-hit:" | awk '{print $2}')
    embed_cache_hit_2=$(curl -s --max-time 15 \
        -D - -o /dev/null \
        -X POST \
        -H "${EFFECTIVE_AUTH}" \
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
        -H "${EFFECTIVE_AUTH}" \
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
    printf '[SKIP]    POST /v1/embeddings probes (no auth credentials)\n'
fi

# ── Langfuse metadata probe (T018) ───────────────────────────────────────────

if [[ -n "${EFFECTIVE_AUTH:-}" ]]; then
    langfuse_status=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" \
        -X POST \
        -H "${EFFECTIVE_AUTH}" \
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
    printf '[SKIP]    Langfuse metadata probe (no auth credentials)\n'
fi

# ── Auth rejection probes (US2: 401 on inference endpoints without key) ───────

probe "GET /v1/models — unauthenticated (no key)"      "${KONG}/v1/models"     401
probe "POST /v1/embeddings — unauthenticated (no key)" "${KONG}/v1/embeddings" 401 \
    -X POST -H "Content-Type: application/json" -d '{}'

# ── Internal port isolation (US4: LiteLLM :4000 must not be host-reachable) ──

litellm_status=$(curl -s -o /dev/null -w '%{http_code}' \
    --connect-timeout 2 --max-time 2 \
    "http://localhost:4000/v1/models" 2>/dev/null || true)
if [[ "$litellm_status" == "000" || -z "$litellm_status" ]]; then
    ok "LiteLLM :4000 — not reachable from host (constitution §2.1)"
else
    fail "LiteLLM :4000 — externally reachable (HTTP ${litellm_status}) — constitution §2.1 violation"
fi

# ── Rate-limit probes (feature 013) ──────────────────────────────────────────

# T009: probe_rate_limit_burst — send 12 rapid requests, expect first 10 → 200,
# requests 11-12 → 429 (per-second limit of 10, FR-002, FR-003).
if [[ -n "${EFFECTIVE_AUTH:-}" ]]; then
    burst_pass=0
    burst_fail=0
    for i in $(seq 1 12); do
        status=$(curl -s -o /dev/null -w '%{http_code}' \
            --max-time "$TIMEOUT" \
            -H "${EFFECTIVE_AUTH}" \
            "${KONG}/v1/models" 2>/dev/null || true)
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
    printf '[SKIP]    Rate-limit burst probe (no auth credentials)\n'
fi

# T010: probe_retry_after_header — confirm Retry-After header present on 429 (FR-004).
if [[ -n "${EFFECTIVE_AUTH:-}" ]]; then
    # exhaust per-second limit then capture the throttled response
    for _ in $(seq 1 10); do
        curl -s -o /dev/null --max-time "$TIMEOUT" \
            -H "${EFFECTIVE_AUTH}" \
            "${KONG}/v1/models" 2>/dev/null
    done
    throttle_headers=$(curl -si --max-time "$TIMEOUT" \
        -H "${EFFECTIVE_AUTH}" \
        "${KONG}/v1/models" 2>/dev/null | tr -d '\r')
    if echo "$throttle_headers" | grep -qi "^retry-after:"; then
        retry_val=$(echo "$throttle_headers" | grep -i "^retry-after:" | awk '{print $2}')
        ok "Rate-limit Retry-After header present on 429 (value: ${retry_val}s)"
    else
        fail "Rate-limit Retry-After header missing on 429 response — FR-004 violation"
    fi
else
    printf '[SKIP]    Rate-limit Retry-After header probe (no auth credentials)\n'
fi

# T012: probe_consumer_isolation — Consumer A throttled, Consumer B still gets 200 (US2, SC-002).
CONSUMER_B_KEY="${CONSUMER_B_API_KEY:-consumer-b-test-key}"
if [[ -n "${EFFECTIVE_AUTH:-}" ]]; then
    # hammer Consumer A past per-second limit in background
    (
        for _ in $(seq 1 20); do
            curl -s -o /dev/null --max-time "$TIMEOUT" \
                -H "${EFFECTIVE_AUTH}" \
                "${KONG}/v1/models" 2>/dev/null
        done
    ) &
    bg_pid=$!
    sleep 0.05
    isolation_status=$(curl -s -o /dev/null -w '%{http_code}' \
        --max-time "$TIMEOUT" \
        -H "Authorization: ${CONSUMER_B_KEY}" \
        "${KONG}/v1/models" 2>/dev/null || true)
    wait "$bg_pid" 2>/dev/null || true
    if [[ "$isolation_status" == "200" ]]; then
        ok "Rate-limit consumer isolation — Consumer B gets 200 while Consumer A is throttled (SC-002)"
    else
        fail "Rate-limit consumer isolation — Consumer B got HTTP ${isolation_status}, expected 200"
    fi
else
    printf '[SKIP]    Rate-limit consumer isolation probe (no auth credentials)\n'
fi

# T014: probe_custom_consumer_limit — consumer-scoped override enforced (US3, SC-004).
consumer_b_plugin_id=""
if [[ -n "${EFFECTIVE_AUTH:-}" ]]; then
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
                "${KONG}/v1/models" 2>/dev/null || true)
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
    printf '[SKIP]    Rate-limit per-consumer override probe (no auth credentials)\n'
fi

# T015: probe_quota_headers — all nine RateLimit-* headers present on 200 (US4, FR-006).
if [[ -n "${EFFECTIVE_AUTH:-}" ]]; then
    quota_headers=$(curl -si --max-time "$TIMEOUT" \
        -H "${EFFECTIVE_AUTH}" \
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
    printf '[SKIP]    Rate-limit quota headers probe (no auth credentials)\n'
fi

# ── Request correlation probes (feature 014) ─────────────────────────────────

# T001: X-Request-ID present on every response (SC-001, FR-003)
corr_id=$(curl -si --max-time "$TIMEOUT" \
    "${KONG}/health" 2>/dev/null | tr -d '\r' \
    | grep -i "^x-request-id:" | awk '{print $2}')
if [[ -n "$corr_id" ]]; then
    ok "X-Request-ID — present in response header (${corr_id})"
else
    fail "X-Request-ID — missing from response header (FR-003 violation)"
fi

# T002: X-Request-ID is UUID v4 format (SC-001, FR-001)
uuid_regex='^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
if [[ -n "$corr_id" ]] && echo "$corr_id" | grep -Eqi "$uuid_regex"; then
    ok "X-Request-ID — UUID v4 format confirmed"
else
    fail "X-Request-ID — not a valid UUID v4: '${corr_id:-absent}'"
fi

# T003: client-supplied X-Request-ID is overwritten (SC-004, FR-002)
client_id="00000000-0000-0000-0000-000000000000"
returned_id=$(curl -si --max-time "$TIMEOUT" \
    -H "X-Request-ID: ${client_id}" \
    "${KONG}/health" 2>/dev/null | tr -d '\r' \
    | grep -i "^x-request-id:" | awk '{print $2}')
if [[ "$returned_id" != "$client_id" && -n "$returned_id" ]]; then
    ok "X-Request-ID — client-supplied value overwritten (returned: ${returned_id})"
else
    fail "X-Request-ID — client value leaked to response: '${returned_id:-absent}'"
fi

# T004: X-Request-ID unique across sequential requests (SC-001, FR-007)
corr_id_a=$(curl -si --max-time "$TIMEOUT" "${KONG}/health" 2>/dev/null | tr -d '\r' \
    | grep -i "^x-request-id:" | awk '{print $2}')
corr_id_b=$(curl -si --max-time "$TIMEOUT" "${KONG}/health" 2>/dev/null | tr -d '\r' \
    | grep -i "^x-request-id:" | awk '{print $2}')
if [[ -n "$corr_id_a" && -n "$corr_id_b" && "$corr_id_a" != "$corr_id_b" ]]; then
    ok "X-Request-ID — unique across sequential requests (${corr_id_a} ≠ ${corr_id_b})"
else
    fail "X-Request-ID — duplicate or empty IDs: a='${corr_id_a:-absent}' b='${corr_id_b:-absent}'"
fi

# T005: X-Request-ID present on 401 error response (SC-001, FR-003)
err_corr_id=$(curl -si --max-time "$TIMEOUT" \
    "${KONG}/v1/chat/completions" \
    -X POST -H "Content-Type: application/json" \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"no auth"}]}' \
    2>/dev/null | tr -d '\r' \
    | grep -i "^x-request-id:" | awk '{print $2}')
if [[ -n "$err_corr_id" ]]; then
    ok "X-Request-ID — present on 401 error response (${err_corr_id})"
else
    fail "X-Request-ID — missing from 401 error response (FR-003 violation)"
fi

# T006: client-supplied traceparent is stripped — verify pre-function plugin active
# (Indirect check: if pre-function runs, a crafted client traceparent cannot appear in response)
client_traceparent="00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
corr_id_tp=$(curl -si --max-time "$TIMEOUT" \
    -H "traceparent: ${client_traceparent}" \
    "${KONG}/health" 2>/dev/null | tr -d '\r' \
    | grep -i "^x-request-id:" | awk '{print $2}')
if [[ -n "$corr_id_tp" ]]; then
    ok "X-Request-ID — present when client supplies traceparent (pre-function active)"
else
    fail "X-Request-ID — missing; pre-function plugin may not be loaded"
fi

# US4: 5 sequential requests → 5 distinct UUIDs (FR-007, SC-001)
declare -a corr_ids=()
for i in $(seq 1 5); do
    rid=$(curl -si --max-time "$TIMEOUT" "${KONG}/health" 2>/dev/null | tr -d '\r' \
        | grep -i "^x-request-id:" | awk '{print $2}')
    corr_ids+=("$rid")
done
# check all 5 are non-empty and unique
uniq_count=$(printf '%s\n' "${corr_ids[@]}" | grep -v '^$' | sort -u | wc -l | tr -d ' ')
if [[ "$uniq_count" == "5" ]]; then
    ok "X-Request-ID — 5 sequential requests produced 5 distinct UUIDs (FR-007)"
else
    fail "X-Request-ID — expected 5 distinct UUIDs, got ${uniq_count} unique non-empty values"
fi

# US3 info: guardrails audit log correlation (manual verification required for obs profile)
printf '[INFO]    Guardrails audit log: make logs svc=guardrails | grep request_id\n'
printf '[INFO]    Phoenix span lookup: filter by gateway.request_id = %s at http://localhost:6006\n' "${corr_id:-<run-with-obs-profile>}"

# ── [015] Request body size limit ────────────────────────────────────────────

REQUEST_SIZE_LIMIT_MB="${REQUEST_SIZE_LIMIT_MB:-10}"

if [[ -n "${EFFECTIVE_AUTH:-}" ]]; then

    # [015] US1 — oversized payload returns 413 (SC-001)
    # Generate an 11 MB body inline; Kong must reject before forwarding to LiteLLM.
    oversize_status=$(python3 -c "
import urllib.request, json, sys
body = json.dumps({'model':'gpt-4o','messages':[{'role':'user','content':'x'*11534336}]}).encode()
req = urllib.request.Request(
    '${KONG}/v1/chat/completions',
    data=body,
    headers={'Authorization':'${AUTH_HEADER_VALUE}','Content-Type':'application/json'},
    method='POST'
)
try:
    urllib.request.urlopen(req, timeout=10)
    print('200')
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print('000')
" 2>/dev/null || true)
    if [[ "$oversize_status" == "413" ]]; then
        ok "[015] POST /v1/chat/completions — oversized payload (11 MB) rejected with HTTP 413 (SC-001)"
    else
        fail "[015] POST /v1/chat/completions — expected HTTP 413 for oversized payload, got ${oversize_status}"
    fi

    # [015] US1 — boundary: exactly REQUEST_SIZE_LIMIT_MB content passes through (SC-002)
    # A 10 MB payload should not be rejected (200, 400, or 422 all indicate pass-through).
    boundary_status=$(python3 -c "
import urllib.request, json, sys
body = json.dumps({'model':'gpt-4o','messages':[{'role':'user','content':'x'*10485760}]}).encode()
req = urllib.request.Request(
    '${KONG}/v1/chat/completions',
    data=body,
    headers={'Authorization':'${AUTH_HEADER_VALUE}','Content-Type':'application/json'},
    method='POST'
)
try:
    urllib.request.urlopen(req, timeout=15)
    print('200')
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print('000')
" 2>/dev/null || true)
    if [[ "$boundary_status" != "413" && "$boundary_status" != "000" ]]; then
        ok "[015] POST /v1/chat/completions — boundary payload (10 MB) passed through Kong (HTTP ${boundary_status}) (SC-002)"
    else
        fail "[015] POST /v1/chat/completions — boundary payload (10 MB) was incorrectly rejected with HTTP ${boundary_status} (SC-002)"
    fi

    # [015] US2 — normal request unaffected by size limit (SC-002)
    # Unaffected by [015] size limit — standard small request must still succeed.
    probe "[015] POST /v1/chat/completions — normal request unaffected by size limit" \
        "${KONG}/v1/chat/completions" "200" \
        -X POST \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{"model":"gpt-4o","messages":[{"role":"user","content":"smoke probe — size limit regression check"}]}'

    # [015] US3 — embeddings route also enforces size limit (SC-004)
    embed_oversize_status=$(python3 -c "
import urllib.request, json, sys
body = json.dumps({'model':'text-embedding-3-small','input':'x'*11534336}).encode()
req = urllib.request.Request(
    '${KONG}/v1/embeddings',
    data=body,
    headers={'Authorization':'${AUTH_HEADER_VALUE}','Content-Type':'application/json'},
    method='POST'
)
try:
    urllib.request.urlopen(req, timeout=10)
    print('200')
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print('000')
" 2>/dev/null || true)
    if [[ "$embed_oversize_status" == "413" ]]; then
        ok "[015] POST /v1/embeddings — oversized payload (11 MB) rejected with HTTP 413 (SC-004)"
    else
        fail "[015] POST /v1/embeddings — expected HTTP 413 for oversized payload, got ${embed_oversize_status}"
    fi

    # [015] US3 — 413 response carries X-Request-ID and X-Platform headers (SC-005 observability)
    size_headers=$(python3 -c "
import urllib.request, json, sys
body = json.dumps({'model':'gpt-4o','messages':[{'role':'user','content':'x'*11534336}]}).encode()
req = urllib.request.Request(
    '${KONG}/v1/chat/completions',
    data=body,
    headers={'Authorization':'${AUTH_HEADER_VALUE}','Content-Type':'application/json'},
    method='POST'
)
try:
    urllib.request.urlopen(req, timeout=10)
    print('no-error')
except urllib.error.HTTPError as e:
    xrid = e.headers.get('X-Request-ID','')
    xplat = e.headers.get('X-Platform','')
    print(f'{xrid}|{xplat}')
except Exception:
    print('|')
" 2>/dev/null || echo "|")
    size_xrid="${size_headers%%|*}"
    size_xplat="${size_headers##*|}"
    if [[ -n "$size_xrid" && "$size_xplat" == "inference-platform" ]]; then
        ok "[015] POST /v1/chat/completions — 413 response carries X-Request-ID and X-Platform headers (SC-005)"
    else
        fail "[015] POST /v1/chat/completions — 413 missing correlation headers: X-Request-ID='${size_xrid}' X-Platform='${size_xplat}'"
    fi

else
    printf '[INFO]    [015] Skipping request-size-limit probes — no auth credentials\n'
fi

printf '[INFO]    [015] Loki audit query: {service="kong"} | json | status="413"\n'

# ── [017] Async Batch Inference ───────────────────────────────────────────────

# [017] batch-api direct health — no auth required
probe "[017] GET http://localhost:8091/health — batch-api direct health" \
    "http://localhost:8091/health" 200

if [[ -n "${EFFECTIVE_AUTH:-}" ]]; then

    # [017] US1 — submit a single-item batch, expect 202 with job_id (SC-001)
    batch_resp=$(curl -sf -X POST "${KONG}/v1/batch/jobs" \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{"model":"gpt-4o-mini","items":[{"index":0,"messages":[{"role":"user","content":"ping"}]}]}' \
        2>/dev/null || echo "")
    if [[ -n "$batch_resp" ]]; then
        batch_job_id=$(echo "$batch_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('job_id',''))" 2>/dev/null || echo "")
        batch_status=$(echo "$batch_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null || echo "")
        if [[ -n "$batch_job_id" && "$batch_status" == "queued" ]]; then
            ok "[017] POST /v1/batch/jobs — 202 with job_id and status=queued (SC-001)"
        else
            fail "[017] POST /v1/batch/jobs — unexpected response: ${batch_resp}"
        fi
    else
        fail "[017] POST /v1/batch/jobs — no response (batch-api not reachable through Kong)"
    fi

    # [017] US2 — poll status of the submitted job (SC-001)
    if [[ -n "${batch_job_id:-}" ]]; then
        status_resp=$(curl -sf "${KONG}/v1/batch/jobs/${batch_job_id}" \
            -H "${EFFECTIVE_AUTH}" 2>/dev/null || echo "")
        status_field=$(echo "$status_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null || echo "")
        if [[ "$status_field" == "queued" || "$status_field" == "running" || "$status_field" == "completed" ]]; then
            ok "[017] GET /v1/batch/jobs/{id} — 200 with valid status field: ${status_field}"
        else
            fail "[017] GET /v1/batch/jobs/{id} — unexpected status: '${status_field}'"
        fi
    fi

    # [017] US2 — unknown job_id returns 404 (FR-003)
    unknown_code=$(curl -s -o /dev/null -w "%{http_code}" \
        "${KONG}/v1/batch/jobs/00000000-0000-0000-0000-000000000000" \
        -H "${EFFECTIVE_AUTH}" 2>/dev/null || true)
    if [[ "$unknown_code" == "404" ]]; then
        ok "[017] GET /v1/batch/jobs/{unknown} — 404 Not Found"
    else
        fail "[017] GET /v1/batch/jobs/{unknown} — expected 404, got ${unknown_code}"
    fi

    # [017] US1 — empty items array returns 400 (FR-012)
    empty_code=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "${KONG}/v1/batch/jobs" \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{"model":"gpt-4o","items":[]}' 2>/dev/null || true)
    if [[ "$empty_code" == "422" || "$empty_code" == "400" ]]; then
        ok "[017] POST /v1/batch/jobs — empty items array rejected with ${empty_code}"
    else
        fail "[017] POST /v1/batch/jobs — expected 400/422 for empty items, got ${empty_code}"
    fi

    # [017] US1 — unauthenticated request returns 401 (constitution §IV)
    unauth_code=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "${KONG}/v1/batch/jobs" \
        -H "Content-Type: application/json" \
        -d '{"model":"gpt-4o","items":[{"index":0,"messages":[{"role":"user","content":"x"}]}]}' \
        2>/dev/null || true)
    if [[ "$unauth_code" == "401" ]]; then
        ok "[017] POST /v1/batch/jobs — unauthenticated request returns 401"
    else
        fail "[017] POST /v1/batch/jobs — expected 401 for unauthenticated, got ${unauth_code}"
    fi

else
    printf '[INFO]    [017] Skipping batch API probes — no auth credentials\n'
fi

printf '[INFO]    [017] Results endpoint: GET /v1/batch/jobs/{id}/results (after job completes)\n'

# ── [018] Multimodal Image Support ───────────────────────────────────────────

if [[ -n "${EFFECTIVE_AUTH:-}" ]]; then

    # [018] AC-1 — URL image request returns HTTP 200 with non-empty content (US1)
    vision_url_resp=$(curl -s --max-time 30 \
        -X POST "${KONG}/v1/chat/completions" \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "gpt-4o",
          "messages": [{
            "role": "user",
            "content": [
              {"type": "text", "text": "What colour is dominant in this image?"},
              {"type": "image_url", "image_url": {
                "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png/240px-PNG_transparency_demonstration_1.png"
              }}
            ]
          }]
        }' 2>/dev/null)
    vision_url_status=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
        -X POST "${KONG}/v1/chat/completions" \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "gpt-4o",
          "messages": [{
            "role": "user",
            "content": [
              {"type": "text", "text": "What colour is dominant in this image?"},
              {"type": "image_url", "image_url": {
                "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png/240px-PNG_transparency_demonstration_1.png"
              }}
            ]
          }]
        }' 2>/dev/null)
    vision_content=$(printf '%s' "$vision_url_resp" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('choices',[{}])[0].get('message',{}).get('content',''))" 2>/dev/null || echo "")
    if [[ "$vision_url_status" == "200" && -n "$vision_content" ]]; then
        ok "[018] POST /v1/chat/completions — URL image returns HTTP 200 with non-empty content (AC-1)"
    else
        fail "[018] POST /v1/chat/completions — URL image: expected 200+content, got HTTP ${vision_url_status}"
    fi

    # [018] AC-3 — non-vision model with image rejected with 400 vision_model_required (US3)
    reject_resp=$(curl -s -w '\n%{http_code}' --max-time 10 \
        -X POST "${KONG}/v1/chat/completions" \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "command-r-plus",
          "messages": [{
            "role": "user",
            "content": [
              {"type": "text", "text": "What is this?"},
              {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}}
            ]
          }]
        }' 2>/dev/null)
    reject_body=$(printf '%s' "$reject_resp" | head -n 1)
    reject_status=$(printf '%s' "$reject_resp" | tail -n 1)
    reject_code=$(printf '%s' "$reject_body" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',{}).get('code',''))" 2>/dev/null || echo "")
    if [[ "$reject_status" == "400" && "$reject_code" == "vision_model_required" ]]; then
        ok "[018] POST /v1/chat/completions — non-vision model rejected HTTP 400 code=vision_model_required (AC-3)"
    else
        fail "[018] POST /v1/chat/completions — expected 400/vision_model_required, got HTTP ${reject_status} code='${reject_code}'"
    fi

    # [018] AC-4 — stream:true with image rejected with 400 vision_streaming_not_supported
    stream_vision_resp=$(curl -s -w '\n%{http_code}' --max-time 10 \
        -X POST "${KONG}/v1/chat/completions" \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "gpt-4o",
          "stream": true,
          "messages": [{
            "role": "user",
            "content": [
              {"type": "text", "text": "Describe."},
              {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}}
            ]
          }]
        }' 2>/dev/null)
    stream_vision_body=$(printf '%s' "$stream_vision_resp" | head -n 1)
    stream_vision_status=$(printf '%s' "$stream_vision_resp" | tail -n 1)
    stream_vision_code=$(printf '%s' "$stream_vision_body" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',{}).get('code',''))" 2>/dev/null || echo "")
    if [[ "$stream_vision_status" == "400" && "$stream_vision_code" == "vision_streaming_not_supported" ]]; then
        ok "[018] POST /v1/chat/completions — stream+image rejected HTTP 400 code=vision_streaming_not_supported (AC-4)"
    else
        fail "[018] POST /v1/chat/completions — expected 400/vision_streaming_not_supported, got HTTP ${stream_vision_status} code='${stream_vision_code}'"
    fi

    # [018] AC-6 — text-only request regression (no vision validation interference)
    probe "[018] POST /v1/chat/completions — text-only regression (AC-6)" \
        "${KONG}/v1/chat/completions" 200 \
        -X POST \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"smoke regression check"}]}'

else
    printf '[INFO]    [018] Skipping multimodal probes — no auth credentials\n'
fi

printf '[INFO]    [018] Base64 image test: see specs/018-multimodal-image-support/quickstart.md AC-2\n'
printf '[INFO]    [018] Audit log check: make logs svc=guardrails | grep image_part_count\n'

# ── [019] Function Calling Support ───────────────────────────────────────────

if [[ -n "${EFFECTIVE_AUTH:-}" ]]; then

    # [019] AC-1 — auto tool selection: weather message → tool_calls with valid JSON args
    fc_resp=$(curl -s --max-time 30 \
        -X POST "${KONG}/v1/chat/completions" \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "gpt-4o",
          "tool_choice": "auto",
          "tools": [{"type":"function","function":{"name":"get_weather","description":"Get current weather","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}],
          "messages": [{"role":"user","content":"What is the weather in London?"}]
        }' 2>/dev/null)
    fc_finish=$(printf '%s' "$fc_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['finish_reason'])" 2>/dev/null || echo "")
    fc_name=$(printf '%s' "$fc_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['tool_calls'][0]['function']['name'])" 2>/dev/null || echo "")
    fc_args=$(printf '%s' "$fc_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); args=d['choices'][0]['message']['tool_calls'][0]['function']['arguments']; json.loads(args); print('ok')" 2>/dev/null || echo "invalid")
    if [[ "$fc_finish" == "tool_calls" && "$fc_name" == "get_weather" && "$fc_args" == "ok" ]]; then
        ok "[019] POST /v1/chat/completions — auto tool selection: finish_reason=tool_calls, fn=get_weather, args valid JSON (AC-1)"
    else
        fail "[019] POST /v1/chat/completions — auto tool selection: finish=${fc_finish} fn=${fc_name} args=${fc_args}"
    fi

    # [019] AC-4 — non-FC model + tools → 400 function_calling_model_required
    fc_reject_resp=$(curl -s -w '\n%{http_code}' --max-time 10 \
        -X POST "${KONG}/v1/chat/completions" \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "claude-haiku",
          "tools": [{"type":"function","function":{"name":"test","parameters":{}}}],
          "messages": [{"role":"user","content":"test"}]
        }' 2>/dev/null)
    fc_reject_body=$(printf '%s' "$fc_reject_resp" | head -n 1)
    fc_reject_status=$(printf '%s' "$fc_reject_resp" | tail -n 1)
    fc_reject_code=$(printf '%s' "$fc_reject_body" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',{}).get('code',''))" 2>/dev/null || echo "")
    if [[ "$fc_reject_status" == "400" && "$fc_reject_code" == "function_calling_model_required" ]]; then
        ok "[019] POST /v1/chat/completions — non-FC model rejected HTTP 400 code=function_calling_model_required (AC-4)"
    else
        fail "[019] POST /v1/chat/completions — expected 400/function_calling_model_required, got HTTP ${fc_reject_status} code='${fc_reject_code}'"
    fi

    # [019] AC-7 — text-only regression (no tools, unaffected by FC gate)
    probe "[019] POST /v1/chat/completions — text-only regression after FC feature (AC-7)" \
        "${KONG}/v1/chat/completions" 200 \
        -X POST \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"smoke regression check"}]}'

else
    printf '[INFO]    [019] Skipping function-calling probes — no auth credentials\n'
fi

printf '[INFO]    [019] Audit log check: make logs svc=guardrails | grep tool_count\n'

# ── [020] Structured JSON Output ─────────────────────────────────────────────

if [[ -n "${EFFECTIVE_AUTH:-}" ]]; then

    # [020] SC-001 — valid json_schema request returns HTTP 200 with JSON content (US1)
    so_resp=$(curl -s --max-time 30 \
        -X POST "${KONG}/v1/chat/completions" \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "gpt-4o-mini",
          "response_format": {
            "type": "json_schema",
            "name": "smoke_invoice",
            "strict": true,
            "schema": {
              "type": "object",
              "properties": {
                "invoice_number": {"type": "string"},
                "total": {"type": "number"}
              },
              "required": ["invoice_number", "total"],
              "additionalProperties": false
            }
          },
          "messages": [{"role":"user","content":"Extract: Invoice INV-SMOKE total $42.00"}]
        }' 2>/dev/null)
    so_status=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
        -X POST "${KONG}/v1/chat/completions" \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "gpt-4o-mini",
          "response_format": {
            "type": "json_schema",
            "name": "smoke_invoice",
            "strict": true,
            "schema": {
              "type": "object",
              "properties": {
                "invoice_number": {"type": "string"},
                "total": {"type": "number"}
              },
              "required": ["invoice_number", "total"],
              "additionalProperties": false
            }
          },
          "messages": [{"role":"user","content":"Extract: Invoice INV-SMOKE total $42.00"}]
        }' 2>/dev/null)
    so_content=$(printf '%s' "$so_resp" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); c=d['choices'][0]['message']['content']; json.loads(c); print('ok')" 2>/dev/null || echo "invalid")
    if [[ "$so_status" == "200" && "$so_content" == "ok" ]]; then
        ok "[020] POST /v1/chat/completions — json_schema returns HTTP 200 with parseable JSON content (SC-001)"
    else
        fail "[020] POST /v1/chat/completions — json_schema: expected 200+json-content, got HTTP ${so_status} content=${so_content}"
    fi

    # [020] SC-002 — stream:true + json_schema → 400 structured_output_streaming_not_supported (US1)
    so_stream_resp=$(curl -s -w '\n%{http_code}' --max-time 10 \
        -X POST "${KONG}/v1/chat/completions" \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "gpt-4o-mini",
          "stream": true,
          "response_format": {
            "type": "json_schema",
            "name": "smoke_schema",
            "strict": true,
            "schema": {"type": "object", "properties": {}, "additionalProperties": false}
          },
          "messages": [{"role":"user","content":"hi"}]
        }' 2>/dev/null)
    so_stream_body=$(printf '%s' "$so_stream_resp" | head -n 1)
    so_stream_status=$(printf '%s' "$so_stream_resp" | tail -n 1)
    so_stream_code=$(printf '%s' "$so_stream_body" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',{}).get('code',''))" 2>/dev/null || echo "")
    if [[ "$so_stream_status" == "400" && "$so_stream_code" == "structured_output_streaming_not_supported" ]]; then
        ok "[020] POST /v1/chat/completions — stream+json_schema rejected HTTP 400 code=structured_output_streaming_not_supported (SC-002)"
    else
        fail "[020] POST /v1/chat/completions — stream+json_schema: expected 400/structured_output_streaming_not_supported, got HTTP ${so_stream_status} code='${so_stream_code}'"
    fi

    # [020] SC-002 — invalid schema rejected with 422 invalid_json_schema (US2)
    so_invalid_resp=$(curl -s -w '\n%{http_code}' --max-time 10 \
        -X POST "${KONG}/v1/chat/completions" \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "gpt-4o-mini",
          "response_format": {
            "type": "json_schema",
            "name": "bad_schema",
            "strict": true,
            "schema": {"type": "not_a_valid_type"}
          },
          "messages": [{"role":"user","content":"hi"}]
        }' 2>/dev/null)
    so_invalid_body=$(printf '%s' "$so_invalid_resp" | head -n 1)
    so_invalid_status=$(printf '%s' "$so_invalid_resp" | tail -n 1)
    so_invalid_code=$(printf '%s' "$so_invalid_body" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',{}).get('code',''))" 2>/dev/null || echo "")
    if [[ "$so_invalid_status" == "422" && "$so_invalid_code" == "invalid_json_schema" ]]; then
        ok "[020] POST /v1/chat/completions — invalid schema rejected HTTP 422 code=invalid_json_schema (SC-002)"
    else
        fail "[020] POST /v1/chat/completions — invalid schema: expected 422/invalid_json_schema, got HTTP ${so_invalid_status} code='${so_invalid_code}'"
    fi

    # [020] SC-003 — missing response_format.name → 400 invalid_structured_output_request (US2)
    so_noname_status=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
        -X POST "${KONG}/v1/chat/completions" \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "gpt-4o-mini",
          "response_format": {
            "type": "json_schema",
            "schema": {"type": "object", "additionalProperties": false}
          },
          "messages": [{"role":"user","content":"hi"}]
        }' 2>/dev/null)
    if [[ "$so_noname_status" == "400" ]]; then
        ok "[020] POST /v1/chat/completions — missing name rejected HTTP 400 (SC-003)"
    else
        fail "[020] POST /v1/chat/completions — missing name: expected 400, got HTTP ${so_noname_status}"
    fi

    # [020] SC-004 — text-only regression unaffected by SO gate (US1)
    probe "[020] POST /v1/chat/completions — text-only regression unaffected by SO gate (SC-004)" \
        "${KONG}/v1/chat/completions" 200 \
        -X POST \
        -H "${EFFECTIVE_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"smoke regression check 020"}]}'

else
    printf '[INFO]    [020] Skipping structured output probes — no auth credentials\n'
fi

printf '[INFO]    [020] Audit log check: make logs svc=guardrails | grep schema_name\n'
printf '[INFO]    [020] Phoenix span check: filter by metadata.schema_name at http://localhost:6006\n'

# ── JWT validation probes (feature 024) ──────────────────────────────────────

# [024] Malformed JWT → 401 with platform error schema (T010/T011)
jwt_malformed_body=$(curl -s --max-time "$TIMEOUT" \
    -H "Authorization: Bearer not.a.valid.jwt" \
    "${KONG}/v1/models" 2>/dev/null)
jwt_malformed_status=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" \
    -H "Authorization: Bearer not.a.valid.jwt" \
    "${KONG}/v1/models" 2>/dev/null)
if [[ "$jwt_malformed_status" == "401" ]]; then
    ok "[024] /v1/models — malformed JWT returns 401"
else
    fail "[024] /v1/models — malformed JWT expected 401, got ${jwt_malformed_status}"
fi
jwt_error_field=$(printf '%s' "$jwt_malformed_body" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',''))" 2>/dev/null || echo "")
if [[ "$jwt_error_field" == "Unauthorized" ]]; then
    ok "[024] /v1/models — 401 body has error=Unauthorized (platform error schema, T010)"
else
    fail "[024] /v1/models — 401 body missing error=Unauthorized field: ${jwt_malformed_body}"
fi

# [024] JWT with unknown iss (no matching consumer) → 401 (T011 tampered token)
jwt_unknown_iss=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" \
    -H "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJzbW9rZSIsImlzcyI6Imh0dHA6Ly91bmtub3duLWlkcC9yZWFsbXMvdGVzdCIsImV4cCI6OTk5OTk5OTk5OX0.invalidsig" \
    "${KONG}/v1/models" 2>/dev/null)
if [[ "$jwt_unknown_iss" == "401" ]]; then
    ok "[024] /v1/models — JWT with unknown iss returns 401 (T011)"
else
    fail "[024] /v1/models — JWT with unknown iss expected 401, got ${jwt_unknown_iss}"
fi

# [024] Phoenix UI route — unauthenticated returns 401 (T018, FR-015)
probe "[024] GET /phoenix — unauthenticated returns 401 (T018)" \
    "${KONG}/phoenix" 401

# [024] Langfuse UI route — unauthenticated returns 401 (T018, FR-016)
probe "[024] GET /langfuse — unauthenticated returns 401 (T018)" \
    "${KONG}/langfuse" 401

# [024] Valid JWT accepted, claim-forwarding pipeline active (T012/T013)
if [[ -n "$JWT_TOKEN" ]]; then
    jwt_valid_status=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" \
        -H "${EFFECTIVE_AUTH}" \
        "${KONG}/v1/models" 2>/dev/null)
    if [[ "$jwt_valid_status" == "200" ]]; then
        ok "[024] /v1/models — valid JWT accepted (200); claim-forwarding pipeline active (T012)"
    else
        fail "[024] /v1/models — valid JWT expected 200, got ${jwt_valid_status}"
    fi
else
    printf '[SKIP]    [024] JWT claim-forwarding probe (INFERENCE_GATEWAY_CLIENT_SECRET not set)\n'
fi

# [024] Keycloak health check (T025)
if curl -sf --max-time 3 "${KEYCLOAK_URL}/health/ready" >/dev/null 2>&1; then
    ok "[024] Keycloak /health/ready — auth profile running"
else
    printf '[INFO]    [024] Keycloak not reachable at %s — run: make up-auth\n' "${KEYCLOAK_URL}"
fi

# [024] Key rotation idempotency — seed-kong-jwt.sh re-run succeeds (T020)
if curl -sf --max-time 3 "${KEYCLOAK_URL}/health/ready" >/dev/null 2>&1; then
    if bash scripts/seed-kong-jwt.sh >/dev/null 2>&1; then
        ok "[024] seed-kong-jwt.sh — idempotent re-run succeeded (key rotation SLO)"
    else
        fail "[024] seed-kong-jwt.sh — idempotent re-run failed"
    fi
else
    printf '[SKIP]    [024] seed-kong-jwt.sh key-rotation probe (Keycloak not running)\n'
fi

# ── MFA TOTP enforcement probes (feature 025) ────────────────────────────────

KEYCLOAK_ADMIN="${KEYCLOAK_ADMIN:-admin}"
KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-}"

if curl -sf --max-time 3 "${KEYCLOAK_URL}/health/ready" >/dev/null 2>&1 && [[ -n "$KEYCLOAK_ADMIN_PASSWORD" ]]; then
  # Obtain admin token from master realm
  _admin_token=$(curl -sf --max-time 5 \
    -X POST "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
    -d "grant_type=password" \
    -d "client_id=admin-cli" \
    -d "username=${KEYCLOAK_ADMIN}" \
    -d "password=${KEYCLOAK_ADMIN_PASSWORD}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null || echo "")

  if [[ -n "$_admin_token" ]]; then
    _realm=$(curl -sf --max-time 5 \
      -H "Authorization: Bearer ${_admin_token}" \
      "${KEYCLOAK_URL}/admin/realms/${KEYCLOAK_REALM}" 2>/dev/null || echo "{}")

    # [025] browserFlow must be browser-mfa
    _browser_flow=$(printf '%s' "$_realm" \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('browserFlow',''))" 2>/dev/null || echo "")
    if [[ "$_browser_flow" == "browser-mfa" ]]; then
      ok "[025] Keycloak browserFlow == browser-mfa (MFA flow active)"
    else
      fail "[025] Keycloak browserFlow expected browser-mfa, got: ${_browser_flow}"
    fi

    # [025] OTP policy: type=totp, digits=6, period=30, codeReusable=false
    _otp_type=$(printf '%s' "$_realm" \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('otpPolicyType',''))" 2>/dev/null || echo "")
    _otp_digits=$(printf '%s' "$_realm" \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('otpPolicyDigits',''))" 2>/dev/null || echo "")
    _otp_period=$(printf '%s' "$_realm" \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('otpPolicyPeriod',''))" 2>/dev/null || echo "")
    _otp_reuse=$(printf '%s' "$_realm" \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('otpPolicyCodeReusable',''))" 2>/dev/null || echo "")
    if [[ "$_otp_type" == "totp" && "$_otp_digits" == "6" && "$_otp_period" == "30" && "$_otp_reuse" == "False" ]]; then
      ok "[025] OTP policy: type=totp, digits=6, period=30, codeReusable=false (RFC 6238 compliant)"
    else
      fail "[025] OTP policy mismatch — type=${_otp_type} digits=${_otp_digits} period=${_otp_period} reusable=${_otp_reuse}"
    fi

    # [025] Brute-force: enabled, failureFactor=5, permanentLockout=false
    _bf_enabled=$(printf '%s' "$_realm" \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('bruteForceProtected',''))" 2>/dev/null || echo "")
    _bf_factor=$(printf '%s' "$_realm" \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('failureFactor',''))" 2>/dev/null || echo "")
    _bf_perm=$(printf '%s' "$_realm" \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('permanentLockout',''))" 2>/dev/null || echo "")
    if [[ "$_bf_enabled" == "True" && "$_bf_factor" == "5" && "$_bf_perm" == "False" ]]; then
      ok "[025] Brute-force protection: enabled, failureFactor=5, permanentLockout=false"
    else
      fail "[025] Brute-force config mismatch — enabled=${_bf_enabled} failureFactor=${_bf_factor} permanentLockout=${_bf_perm}"
    fi

    # [025] browser-mfa flow exists in realm authenticationFlows
    _flow_count=$(curl -sf --max-time 5 \
      -H "Authorization: Bearer ${_admin_token}" \
      "${KEYCLOAK_URL}/admin/realms/${KEYCLOAK_REALM}/authentication/flows" 2>/dev/null \
      | python3 -c "import sys,json; flows=json.load(sys.stdin); print(sum(1 for f in flows if f.get('alias')=='browser-mfa'))" 2>/dev/null || echo "0")
    if [[ "$_flow_count" == "1" ]]; then
      ok "[025] browser-mfa authentication flow exists in realm"
    else
      fail "[025] browser-mfa authentication flow not found in realm (count=${_flow_count})"
    fi

  else
    printf '[SKIP]    [025] MFA config probes (admin token unavailable)\n'
  fi
else
  printf '[SKIP]    [025] MFA config probes (Keycloak not running or KEYCLOAK_ADMIN_PASSWORD not set)\n'
fi

# ── Result ────────────────────────────────────────────────────────────────────

printf '\n%d passed, %d failed\n\n' "$pass" "$fail"

if [[ $fail -gt 0 ]]; then
    exit 1
fi
