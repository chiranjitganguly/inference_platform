# Research: Automatic Model Fallback Routing

**Feature**: `008-model-fallback-routing` | **Date**: 2026-05-28

All decisions below were resolved from user-provided clarifications, existing codebase state, and LiteLLM v1.52.0 documentation. No external research required.

---

## Decision 1: Fallback Chain Configuration Location

**Decision**: Extend `litellm_settings.fallbacks` in `services/litellm/config.yaml`. Do not use `router_settings`.

**Rationale**: The existing config already defines partial fallback chains under `litellm_settings.fallbacks`. LiteLLM v1.52.0 supports both `litellm_settings.fallbacks` (global, all-model) and `router_settings.fallbacks` (router-scoped). Since the platform has a single LiteLLM instance with a single router, both resolve identically. Extending the existing `litellm_settings` key avoids a structural change and preserves the established config pattern.

**Alternatives considered**:
- `router_settings.fallbacks` — functionally equivalent; rejected to avoid introducing a second config block that duplicates the existing `litellm_settings.fallbacks` structure
- Per-model `fallbacks` in each `model_list` entry — rejected: more verbose; `litellm_settings.fallbacks` achieves the same result at the global level

---

## Decision 2: Retry Count and Cooldown Configuration

**Decision**: Add `num_retries: 2`, `allowed_fails: 3`, `cooldown_time: 60` to `litellm_settings`.

**Rationale**: LiteLLM v1.52.0 supports these three keys natively in `litellm_settings`:
- `num_retries`: number of retry attempts per model before advancing to the next fallback (clarification Q3: per-model, not a shared budget)
- `allowed_fails`: consecutive failures before a model enters cooldown (clarification Q1: applies to any model in the chain)
- `cooldown_time`: seconds a model is excluded from routing after hitting `allowed_fails`

No custom code is required for the cooldown mechanism. LiteLLM's router maintains an in-process dict tracking consecutive failure counts per model alias. Cooldown state resets on service restart (acceptable per spec assumptions).

**Alternatives considered**:
- Redis-backed cooldown state — rejected: adds operational complexity for a local-dev platform; the spec explicitly states "per-request state, not persistent"; in-process state is sufficient and simpler
- Custom middleware tracking failures — rejected: LiteLLM's native mechanism is already correct

---

## Decision 3: Context Window Overflow Detection and Routing

**Decision**: Use `litellm_settings.context_window_fallbacks` with explicit per-model overflow targets.

**Rationale**: LiteLLM v1.52.0 detects context window overflow from the provider's `context_length_exceeded` error (after the provider rejects the request) OR from the model's `context_window` field in `model_info` (pre-flight check if configured). Since all models in the catalogue have `model_info.context_window` set, LiteLLM can perform pre-flight token counting. The `context_window_fallbacks` key maps each model to its designated larger-context alternative.

**Overflow target assignments** (based on model catalogue context windows):
| Primary | Context Window | Overflow Target | Overflow Target Window |
|---------|---------------|-----------------|----------------------|
| `gpt-4o` | 128 k | `gpt-4.1` | 1 M |
| `gpt-4o-mini` | 128 k | `gpt-4.1` | 1 M |
| `o4-mini` | 128 k | `gpt-4.1` | 1 M |
| `claude-sonnet` | 200 k | `gemini-pro` | 1 M |
| `claude-haiku` | 200 k | `gemini-pro` | 1 M |
| `command-r-plus` | 128 k | `gpt-4.1` | 1 M |
| `gpt-4.1`, `gemini-pro`, `gemini-flash` | ≥ 1 M | — (no overflow target needed) | — |

**Alternatives considered**:
- Only error-based overflow detection (after provider rejects) — rejected: pre-flight detection avoids a wasted provider API call and associated latency/cost; `model_info.context_window` is already populated in the catalogue

---

## Decision 4: Phoenix Span Attributes for Fallback Events

**Decision**: Custom `FallbackSpanCallback` class in `services/litellm/fallback_callbacks.py`, mounted into the LiteLLM container and registered in `litellm_settings.callbacks`.

**Rationale**: LiteLLM's native `arize_phoenix` callback creates an OpenInference LLM span for each successful provider call. It does not natively add routing-level metadata (`llm.fallback.triggered`, `llm.fallback.reason`, `llm.model.requested`, `llm.fallback.attempt_count`) as span attributes. The clarification confirmed these four attributes are required. LiteLLM's `CustomLogger` base class provides `log_success_event` and `log_failure_event` hooks that receive `kwargs` including `metadata`, `model`, `fallbacks`, and `exception` — enough to populate all four attributes.

**Implementation**: `FallbackSpanCallback` subclasses `litellm.integrations.custom_logger.CustomLogger`. In `log_success_event`, it checks `kwargs.get("metadata", {})` for `"fallback_model_response"` to detect fallback events, then calls `span.set_attribute()` on the active OpenTelemetry span. The file is mounted at `/app/fallback_callbacks.py` inside the container and referenced in config as `fallback_callbacks.FallbackSpanCallback`.

**Span attributes comply with constitution §2.4**: all four attributes are routing metadata — no prompt or response content is included.

**Alternatives considered**:
- Relying solely on Prometheus counters (`litellm_fallback_success_total`) — rejected: the spec requires Phoenix trace view visibility (FR-008, clarification Q2); Prometheus counters alone do not satisfy this
- Modifying the `arize_phoenix` callback source — rejected: would require a custom LiteLLM image, increasing maintenance burden and violating the version-lock principle

---

## Decision 5: 503 Error Body Normalisation

**Decision**: Intercept LiteLLM's 503 response in `services/guardrails/main.py` and reformat the body to the platform schema.

**Rationale**: LiteLLM v1.52.0 returns HTTP 503 with body `{"error": {"message": "...", "type": "...", "code": "503"}}` when all fallbacks are exhausted. The platform's required format (constitution §4.4) is `{"error": "all_fallbacks_exhausted", "message": "...", "detail": {...}}`. The Guardrails service is the only caller of LiteLLM and is already on the return path — it is the correct normalisation point. A simple response body check on `status_code == 503` in the Guardrails proxy handler is all that is required.

**Alternatives considered**:
- LiteLLM custom exception handler (FastAPI middleware inside LiteLLM) — rejected: requires mounting an additional Python file and is architecturally less clean than normalising at the Guardrails boundary, which already owns the request/response lifecycle
- Accepting LiteLLM's native 503 format — rejected: breaks the structured error contract established in features 001–007; callers depending on the `{"error", "message", "detail"}` schema would fail

---

## Decision 6: Fallback Chain Completeness

**Decision**: Extend the existing four partial fallback chains to cover all 9 chat models. Add chains for `gpt-4.1`, `o4-mini`, and `command-r-plus`.

**Rationale**: The existing config defines chains for `gpt-4o`, `gpt-4o-mini`, `claude-sonnet`, and `gemini-flash`. The spec (FR-007, FR-010) requires that models without a configured chain return 503 immediately. Rather than leaving 5 models without chains, this feature completes the catalogue. Models with no reasonable fallback (embeddings) are excluded by design — embedding models do not participate in the chat fallback system.

**Proposed chains for newly configured models**:
- `gpt-4.1` → `[claude-sonnet, gemini-pro]`
- `o4-mini` → `[claude-haiku, gemini-flash]`
- `command-r-plus` → `[gpt-4o, claude-sonnet]`
- `gemini-pro` → `[gpt-4o, claude-sonnet]`
- `claude-haiku` → `[gpt-4o-mini, gemini-flash]`

Embedding models (`text-embedding-3-small`, `text-embedding-3-large`) have no fallback chain — 503 is returned immediately on failure per FR-010.
