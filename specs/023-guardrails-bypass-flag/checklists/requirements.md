# Requirements Checklist: Guardrails Bypass Flag

| ID | Requirement | Implemented | Test |
|---|---|---|---|
| FR-001 | Accept optional `guardrails` boolean in `POST /v1/chat/completions` body | ✅ `proxy()` in `guardrails/main.py` | Test 3, 4 |
| FR-002 | When `false`: skip all validation gates | ✅ bypass branch before validation block | Test 3 |
| FR-003 | When `false` + `stream: true`: true SSE streaming passthrough | ✅ `_streaming_passthrough()` | Test 1 |
| FR-004 | When `false` + `stream: false`: buffered direct passthrough | ✅ direct `client.request()` branch | Test 3 |
| FR-005 | Strip `guardrails` field before forwarding to LiteLLM | ✅ `_pjson.pop("guardrails")` + re-serialise | Test 2 |
| FR-006 | Absent or `true`: all existing behaviour preserved | ✅ guardrails-on path unchanged | Tests 4, 5 |
| FR-007 | Audit log entry written regardless of flag value | ✅ `_write_audit()` called in both bypass branches | Test 7 |
| FR-008 | Flag only applies to `POST /v1/chat/completions` | ✅ extraction gated on `path == "v1/chat/completions"` | — |
