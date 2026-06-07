# Quickstart: Structured JSON Output

**Branch**: `020-structured-json-output` | **Date**: 2026-06-07

---

## Prerequisites

Core stack running:
```bash
make up-core
make seed-kong
```

`API_KEY` set to a valid Kong consumer API key.

---

## SC-001 — 10 consecutive calls all return schema-conforming JSON

Run 10 consecutive requests and assert each `content` field parses and validates:

```bash
for i in $(seq 1 10); do
  RESP=$(curl -s -X POST http://localhost:8080/v1/chat/completions \
    -H "Authorization: Bearer $API_KEY" \
    -H "Content-Type: application/json" \
    -d '{
      "model": "gpt-4o-mini",
      "messages": [{"role": "user", "content": "Extract: Invoice INV-00'"$i"' total $'"$((i * 10))"'.00"}],
      "response_format": {
        "type": "json_schema",
        "name": "invoice_schema",
        "strict": true,
        "schema": {
          "type": "object",
          "properties": {
            "invoice_number": {"type": "string"},
            "total":          {"type": "number"}
          },
          "required": ["invoice_number", "total"],
          "additionalProperties": false
        }
      }
    }')
  CONTENT=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['choices'][0]['message']['content'])")
  echo "Call $i: $CONTENT"
  echo "$CONTENT" | python3 -c "
import sys, json, jsonschema
schema = {'type':'object','properties':{'invoice_number':{'type':'string'},'total':{'type':'number'}},'required':['invoice_number','total'],'additionalProperties':False}
obj = json.loads(sys.stdin.read())
jsonschema.validate(obj, schema)
print('  ✓ valid')
"
done
```

**Expected**: All 10 iterations print `✓ valid`. Zero failures.

---

## SC-002 — Invalid schema rejected before inference (under 100 ms)

```bash
time curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "hi"}],
    "response_format": {
      "type": "json_schema",
      "name": "bad_schema",
      "strict": true,
      "schema": {"type": "bogus_type"}
    }
  }'
```

**Expected**: HTTP `422`. Total elapsed time (from `time`) well under 1 second (no LiteLLM latency).

---

## SC-003 — Schema conformance failure has distinct error type

Trigger with a model and schema that cannot easily conform (use a very constrained schema against a free-form prompt):

```bash
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Tell me a joke"}],
    "response_format": {
      "type": "json_schema",
      "name": "impossible_schema",
      "strict": true,
      "schema": {
        "type": "object",
        "properties": {"x": {"type": "integer", "minimum": 100, "maximum": 5}},
        "required": ["x"],
        "additionalProperties": false
      }
    }
  }' | python3 -m json.tool
```

**Expected response**:
```json
{
  "error": {
    "type": "schema_conformance_failure",
    ...
  },
  "retry_count": 3,
  "schema_name": "impossible_schema"
}
```
HTTP status: `422`. `error.type` is `schema_conformance_failure` — distinct from `all_fallbacks_exhausted` (503) and model errors.

---

## Streaming guard

```bash
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "hi"}],
    "stream": true,
    "response_format": {
      "type": "json_schema",
      "name": "s",
      "strict": true,
      "schema": {"type": "object", "properties": {}, "additionalProperties": false}
    }
  }'
```

**Expected**: HTTP `400`, `error.type: structured_output_streaming_not_supported`.

---

## Non-SO request unaffected

```bash
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello"}]}'
```

**Expected**: HTTP `200`. Standard chat completion response — no `response_format` processing.

---

## Phoenix trace verification

After running any successful structured output request with `make up-obs`:

1. Open Phoenix at `http://localhost:6006`
2. Find the trace by `X-Request-ID` from the response header
3. Confirm the `LLM` span has attribute `metadata.schema_name = "invoice_schema"` (or whichever name you used)

---

## Metric verification

```bash
curl -s http://localhost:9090/api/v1/query \
  --data-urlencode 'query=structured_output_validation_total'
```

**Expected**: Counter labels visible for `status="pass"`, `model="gpt-4o-mini"`, `schema_hash="<12-hex>"`.

---

## Memory check

```bash
make stats
```

**Expected**: Total memory across all containers remains within the `core` profile budget (~620 MB). No new containers were added by this feature.
