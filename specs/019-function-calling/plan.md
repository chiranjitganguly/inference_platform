# Implementation Plan: Function Calling Support

**Branch**: `019-function-calling` | **Date**: 2026-06-06 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/019-function-calling/spec.md`

## Summary

Extend `/v1/chat/completions` to accept a `tools` array and `tool_choice` field, gated by `"function-calling" in model_info.capabilities`. LiteLLM natively forwards these fields to providers — no proxy code is needed for forwarding. The Guardrails service adds a pre-proxy validation gate (capability check, streaming guard, tool definition validation) and a **post-proxy response validation** step (ensures every `function.arguments` is valid JSON before the response reaches the caller). No `config.yaml` changes required: `function-calling` is already declared for all relevant models.

## Technical Context

**Language/Version**: Python 3.12 (Guardrails service)

**Primary Dependencies**: FastAPI, httpx (async) — already present in the guardrails service

**Storage**: None (in-memory `FunctionCallingCapabilityCache`, loaded from LiteLLM `/model/info` at startup)

**Testing**: pytest (contract tests via `tests/contract/`), smoke tests via `scripts/smoke-test.sh`

**Target Platform**: Linux container (arm64 + amd64), Docker Compose `core` profile (guardrails is already in core)

**Performance Goals**: Pre-proxy gate overhead ≤ 5 ms p95; post-proxy response inspection ≤ 2 ms p95

**Constraints**: `tool_calls` argument values MUST NOT appear in any log/trace/audit (Constitution §II); all `/v1/chat/completions` validation rejections use OpenAI error envelope (ADR-018); no new Docker services

**Scale/Scope**: Shares existing per-consumer rate limits; no new limits introduced

## Constitution Check

| Principle | Status | Notes |
|---|---|---|
| **I — Request Flow Integrity** | PASS | Kong → Guardrails → LiteLLM chain unchanged. Validation and response inspection happen inside Guardrails. |
| **II — Prompt Content Ephemeral** | PASS | `function.arguments` values never logged. Audit adds `tool_count` (int) only. Phoenix spans include function names + count, never argument values. |
| **III — OpenAI Compatibility** | PASS | Standard OpenAI `tools`/`tool_choice`/`tool_calls` wire format. All errors use OpenAI envelope (ADR-018). No breaking changes. |
| **IV — Defence in Depth** | PASS | Kong enforces request size. Guardrails adds FC validation at content layer. LiteLLM handles routing. |
| **V — Falsifiable Acceptance Criteria** | PASS | All ACs are curl commands with expected HTTP status + response body assertions. |

## Project Structure

### Documentation (this feature)

```text
specs/019-function-calling/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── contracts/
│   └── chat-completions-function-calling.md
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code Changes

```text
services/
└── guardrails/
    ├── function_calling.py    # NEW: FunctionCallingCapabilityCache + request/response validation
    └── main.py                # MODIFY: load FC cache on startup, pre+post proxy FC gate

scripts/
└── smoke-test.sh              # MODIFY: add [019] function-calling probe cases

tests/
└── contract/
    └── test_function_calling.py   # NEW: contract tests for all acceptance criteria
```

**No changes to**: `services/litellm/config.yaml`, `docker-compose.yml`, `scripts/seed-kong.sh`, or any other service.

**Structure Decision**: Single-service modification. `function_calling.py` is a sibling to `vision.py` — same pattern, independent lifecycle.

## Phase 0: Research

Complete. See [research.md](research.md).

Key resolved decisions:
1. `supports_functions: true` maps to `"function-calling" in capabilities` (existing convention)
2. No `config.yaml` changes needed — capability already declared for 8 chat models
3. `FunctionCallingCapabilityCache` mirrors `VisionCapabilityCache` exactly
4. **New vs. vision**: post-proxy response inspection (FR-016) — guardrails validates `function.arguments` JSON before forwarding to caller
5. Phoenix tool_call attributes flow through LiteLLM's `arize_phoenix` callback — no custom span injection needed
6. All validation rejections: OpenAI envelope per ADR-018

## Phase 1: Design & Contracts

Complete. See linked artifacts below.

### Design decisions

#### `services/guardrails/function_calling.py` (new)

**`FunctionCallingCapabilityCache`**:
- Mirrors `VisionCapabilityCache`; loads from `/model/info`, extracts `"function-calling" in capabilities`
- Fail-fast on startup if LiteLLM unreachable

**`has_tools(body: dict) -> bool`**: Returns `True` if `tools` is a non-empty list.

**`validate_function_calling_request(body, fc_models) -> (dict, int) | None`**:
Validation order (returns on first failure):
1. `stream: true` → 400 `function_calling_streaming_not_supported`
2. Model not in `fc_models` → 400 `function_calling_model_required` (names model, lists valid alternatives)
3. `tool_choice` set but `tools` empty/absent → 400 `tool_choice_requires_tools`
4. `tool_choice` object names function not in `tools` → 400 `tool_choice_function_not_found`
5. Tool missing `name` or `parameters` not a dict → 400 `invalid_tool_definition` (includes index)

**`validate_tool_call_response(response_body: dict) -> (dict, int) | None`** *(new vs. vision)*:
- If `choices[0].message.tool_calls` exists: `json.loads()` each `function.arguments`
- Failure → `({"error": {"type": "upstream_error", "code": "invalid_tool_arguments", ...}}, 502)`

#### `services/guardrails/main.py` changes

- Lifespan: load `FunctionCallingCapabilityCache` alongside `VisionCapabilityCache`, expose as `app.state.fc_cache`
- Pre-proxy: if `has_tools(body)` → run `validate_function_calling_request()`
- Post-proxy: if request had tools and upstream returned 200 → run `validate_tool_call_response()`
- Audit log: add `"tool_count": int` to `_write_audit()` (0 for non-FC requests; no tool names/arguments)

#### `tests/contract/test_function_calling.py`

Tests: auto-select matching message → 200 + tool_calls; auto-select non-matching → 200 + stop; forced tool → 200 + correct name; `tool_choice: "none"` → 200 + no tool_calls; non-FC model → 400; stream + tools → 400; unknown tool_choice function → 400; bad tool definition → 400; text-only regression → 200.

#### `scripts/smoke-test.sh`

Two new `[019]` probes: (1) auto tool selection → 200 `finish_reason: "tool_calls"`; (2) non-FC model rejection → 400 `function_calling_model_required`.

### Artifacts

- [research.md](research.md)
- [data-model.md](data-model.md)
- [contracts/chat-completions-function-calling.md](contracts/chat-completions-function-calling.md)
- [quickstart.md](quickstart.md)

## Acceptance Criteria (falsifiable)

### AC-1: Tool call for matching message

```bash
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","tool_choice":"auto","tools":[{"type":"function","function":{"name":"get_weather","description":"Get weather","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}],"messages":[{"role":"user","content":"What is the weather in London?"}]}' \
  | jq '{finish_reason:.choices[0].finish_reason,fn:.choices[0].message.tool_calls[0].function.name}'
```
**Expected**: `finish_reason:"tool_calls"`, `fn:"get_weather"`.

---

### AC-2: Arguments parse as valid JSON

```bash
# Same request as AC-1:
... | jq '.choices[0].message.tool_calls[0].function.arguments | fromjson'
```
**Expected**: Parses without error; object contains `city` key.

---

### AC-3: Non-matching message → plain text

```bash
curl -s ... -d '{"model":"gpt-4o","tool_choice":"auto","tools":[...],"messages":[{"role":"user","content":"What year did WW2 end?"}]}' \
  | jq '{finish_reason:.choices[0].finish_reason,has_calls:(.choices[0].message.tool_calls!=null)}'
```
**Expected**: `finish_reason:"stop"`, `has_calls:false`.

---

### AC-4: Non-FC model rejected

```bash
curl -s -w "\nHTTP %{http_code}" -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku","tools":[{"type":"function","function":{"name":"test","parameters":{}}}],"messages":[{"role":"user","content":"test"}]}'
```
**Expected**: HTTP 400, `error.code == "function_calling_model_required"`, message names `"claude-haiku"`.

---

### AC-5: Streaming + tools rejected

```bash
curl -s -w "\nHTTP %{http_code}" -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","stream":true,"tools":[{"type":"function","function":{"name":"test","parameters":{}}}],"messages":[{"role":"user","content":"test"}]}'
```
**Expected**: HTTP 400, `error.code == "function_calling_streaming_not_supported"`.

---

### AC-6: No argument values in audit log

```bash
make logs svc=guardrails | grep '"event_type":"inference_request"' | tail -1
```
After sending AC-1: **Expected**: entry contains `"tool_count":1`; entry does NOT contain `"city"`, `"London"`, or any string from `function.arguments`.

---

### AC-7: Text-only regression

```bash
curl -s -w "\nHTTP %{http_code}" -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Hello"}]}'
```
**Expected**: HTTP 200, `choices[0].message.content` is a non-empty string.

## Complexity Tracking

No constitution violations. No complexity justification required.
