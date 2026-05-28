# Implementation Plan: Automatic Model Fallback Routing

**Branch**: `008-model-fallback-routing` | **Date**: 2026-05-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/008-model-fallback-routing/spec.md`

## Summary

Formalise and extend LiteLLM's partially-configured fallback chains into a fully observable, operator-configurable routing layer. Adds `num_retries` (2 per model), `allowed_fails` (3 consecutive → 60 s cooldown), `cooldown_time`, and `context_window_fallbacks` to `litellm_settings` in `services/litellm/config.yaml`. A custom `FallbackSpanCallback` Python file (mounted into the LiteLLM container) adds `llm.fallback.*` span attributes to Phoenix traces. The Guardrails service normalises LiteLLM's 503 error body to the platform's structured schema. No new services are required.

## Technical Context

**Language/Version**: YAML (LiteLLM config), Python 3.11 (custom callback + Guardrails patch), pytest + requests (contract tests)

**Primary Dependencies**: LiteLLM v1.52.0 (native `fallbacks`, `context_window_fallbacks`, `cooldown_time`, `allowed_fails`, `num_retries` in `litellm_settings`); OpenInference SDK (span attribute conventions)

**Storage**: N/A — cooldown state is in-process (dict in LiteLLM router memory; resets on service restart per spec assumptions)

**Testing**: pytest + requests (contract tests in `tests/contract/test_fallback_routing.py`), curl (smoke test extensions in `scripts/smoke-test.sh`)

**Target Platform**: Docker Compose core profile, macOS OrbStack (development)

**Performance Goals**: Fallback adds latency equal to failed-attempt duration(s). No SLA on worst-case path (503 termination). Success-path p95 TTFT target unchanged at < 400 ms.

**Constraints**: Cooldown state is in-process only — resets on `litellm` service restart; no persistent cooldown across restarts. Custom callback file must be importable from `/app/` inside the LiteLLM container. 503 error body must match platform schema `{"error", "message", "detail"}`.

**Scale/Scope**: All 9 chat models in the catalogue; 4 fallback chains already partially defined; context-window overflow routing for the 5 models with context windows ≤ 200 k tokens.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I — Request Flow Integrity | ✅ PASS | Fallback routing happens entirely within LiteLLM's router. Kong → Guardrails → LiteLLM chain is unchanged. The custom callback runs inside the LiteLLM process. |
| II — Prompt Content Ephemeral | ✅ PASS | `llm.fallback.*` span attributes are routing metadata only: model aliases, failure reason codes, attempt counts. No prompt text, response text, or user data. Fully compliant with §2.4. |
| III — OpenAI API Compatibility | ✅ PASS | Successful fallback responses return the OpenAI-compatible schema with `model` field reflecting the fulfilling model. 503 exhaustion uses the platform's structured error schema (established in constitution §4.4 — HTTP 503 is the defined code for exhausted fallbacks). |
| IV — Defence in Depth | ✅ PASS | All three enforcement layers (Kong auth, OPA policy, Guardrails content scan) execute before LiteLLM makes any routing decision. |
| V — Falsifiable Acceptance Criteria | ✅ PASS | All SC-001–SC-007 are verifiable via curl (model field check, HTTP 503 check), Prometheus (fallback counters), and Phoenix span attribute queries. |

## Project Structure

### Documentation (this feature)

```text
specs/008-model-fallback-routing/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── openapi.yaml     # 503 error body schema + model field change
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code Changes

```text
services/litellm/config.yaml              # Add num_retries, allowed_fails, cooldown_time,
                                          # context_window_fallbacks; extend fallbacks chain
services/litellm/fallback_callbacks.py    # NEW — FallbackSpanCallback adds llm.fallback.*
                                          # span attributes to Phoenix traces
docker-compose.yml                        # Add fallback_callbacks.py volume mount to litellm
services/guardrails/main.py               # Normalise LiteLLM 503 error body to platform schema
tests/contract/test_fallback_routing.py   # NEW — contract tests (US1–US4)
scripts/smoke-test.sh                     # Extend with fallback and 503 probes
```

## Complexity Tracking

| Item | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Custom `FallbackSpanCallback` Python file | LiteLLM's native `arize_phoenix` callback does not add `llm.fallback.triggered`, `llm.fallback.reason`, `llm.model.requested`, or `llm.fallback.attempt_count` as Phoenix span attributes. The spec requires these attributes to be visible in LLM trace views. | Using only Prometheus counters (which LiteLLM does emit natively for fallbacks) would satisfy observability but not the Phoenix trace-view requirement from FR-008 and the clarification. No simpler path exists within LiteLLM's callback architecture. |
| Guardrails 503 normalisation | LiteLLM's native 503 body format is `{"error": {"message": "...", "type": "...", "code": "503"}}`, which does not match the platform schema `{"error": "...", "message": "...", "detail": {...}}` required by constitution §4.4. | Accepting LiteLLM's native format would break the structured error contract established across all prior features. Transforming at the Guardrails layer is the correct point (Guardrails is already on the return path). |
