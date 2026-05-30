#!/usr/bin/env bash
# provision-key.sh — create a LiteLLM virtual key with a monthly budget.
#
# Required env vars (set in .env):
#   LITELLM_MASTER_KEY  — master key for the LiteLLM instance
#
# Usage:
#   ./scripts/provision-key.sh --alias <name> [--budget <usd>] [--models <csv>]
#
# Examples:
#   ./scripts/provision-key.sh --alias team-alpha --budget 20.00 --models gpt-4o-mini,claude-haiku
#   ./scripts/provision-key.sh --alias team-beta   # unlimited budget, all models
set -euo pipefail

KONG="${KONG_BASE_URL:-http://localhost:8080}"
MASTER_KEY="${LITELLM_MASTER_KEY:-}"

if [[ -z "$MASTER_KEY" ]]; then
  echo "Error: LITELLM_MASTER_KEY is not set. Source your .env file first." >&2
  exit 1
fi

ALIAS=""
BUDGET="null"
MODELS_CSV=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --alias)   ALIAS="$2";      shift 2 ;;
    --budget)  BUDGET="$2";     shift 2 ;;
    --models)  MODELS_CSV="$2"; shift 2 ;;
    *) echo "Unknown flag: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$ALIAS" ]]; then
  echo "Error: --alias is required." >&2
  exit 1
fi

if [[ -n "$MODELS_CSV" ]]; then
  MODELS_JSON=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1].split(',')))" "$MODELS_CSV")
else
  MODELS_JSON="null"
fi

PAYLOAD=$(python3 -c "
import json, sys
d = {
  'key_alias': sys.argv[1],
  'max_budget': None if sys.argv[2] == 'null' else float(sys.argv[2]),
  'budget_duration': 'monthly',
}
models = json.loads(sys.argv[3])
if models is not None:
    d['models'] = models
print(json.dumps(d))
" "$ALIAS" "$BUDGET" "$MODELS_JSON")

RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X POST "${KONG}/v1/key/generate" \
  -H "Authorization: Bearer ${MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

HTTP_STATUS=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n -1)

if [[ "$HTTP_STATUS" != "200" ]]; then
  echo "Error: key generation failed (HTTP ${HTTP_STATUS})" >&2
  echo "$BODY" | python3 -m json.tool >&2
  exit 1
fi

KEY=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['key'])")
RESET_AT=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('budget_reset_at','N/A'))")

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Virtual key created — SAVE THIS NOW, it cannot be recovered ║"
echo "╠══════════════════════════════════════════════════════════════╣"
printf  "║  Alias:        %-46s ║\n" "$ALIAS"
printf  "║  Key:          %-46s ║\n" "$KEY"
printf  "║  Budget:       %-46s ║\n" "${BUDGET} USD/month"
printf  "║  Next reset:   %-46s ║\n" "$RESET_AT"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
