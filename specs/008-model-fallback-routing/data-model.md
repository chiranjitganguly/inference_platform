# Data Model: Automatic Model Fallback Routing

**Feature**: `008-model-fallback-routing` | **Date**: 2026-05-28

---

## Entities

### FallbackChain

An ordered list of model aliases to attempt when the primary model fails. Stored in `services/litellm/config.yaml` under `litellm_settings.fallbacks`.

| Field | Type | Constraints |
|---|---|---|
| `primary_model` | string | Must match a `model_name` in `model_list`; unique key |
| `fallback_models` | list[string] | Ordered; each entry must match a `model_name` in `model_list`; no duplicates (FR-012) |

**Validation rules**:
- Duplicate entries in `fallback_models` are skipped at routing time (FR-012)
- An empty list is valid — model with no fallback returns 503 immediately on failure (FR-010)

---

### ContextWindowFallback

A single designated larger-context model for a given primary model. Stored under `litellm_settings.context_window_fallbacks`.

| Field | Type | Constraints |
|---|---|---|
| `primary_model` | string | Must match a `model_name` in `model_list` |
| `overflow_model` | string | Must match a `model_name` in `model_list`; must have a larger `context_window` than the primary |

**Overflow model assignments** (derived from model catalogue context windows):

| Primary Model | Primary Context Window | Overflow Model | Overflow Context Window |
|---|---|---|---|
| `gpt-4o` | 128 k | `gpt-4.1` | 1 M |
| `gpt-4o-mini` | 128 k | `gpt-4.1` | 1 M |
| `o4-mini` | 128 k | `gpt-4.1` | 1 M |
| `claude-sonnet` | 200 k | `gemini-pro` | 1 M |
| `claude-haiku` | 200 k | `gemini-pro` | 1 M |
| `command-r-plus` | 128 k | `gpt-4.1` | 1 M |
| `gpt-4.1` | 1 M | — | — |
| `gemini-pro` | 1 M | — | — |
| `gemini-flash` | 1 M | — | — |

---

### CooldownState

Per-model-alias in-process state tracking consecutive failures and cooldown expiry. Maintained by LiteLLM's router. Not persisted — resets on service restart.

| Field | Type | Description |
|---|---|---|
| `model_alias` | string | Matches a `model_name` in `model_list` |
| `consecutive_failures` | int | Increments on each failure; resets to 0 on first success |
| `cooldown_until` | timestamp | Set to `now + 60s` when `consecutive_failures` reaches 3; null otherwise |
| `is_cooled_down` | bool (derived) | True if `now < cooldown_until` |

**State transitions**:

```
                      failure
AVAILABLE ──────────────────────────────────► FAILURE_COUNT_INCREMENTED
    ▲                                               │
    │ success (resets consecutive_failures=0)       │ consecutive_failures == 3
    │                                               ▼
    └────────────────────────── COOLED_DOWN (60 s exclusion window)
                                                    │
                                       60 s elapsed │
                                                    ▼
                                              AVAILABLE (cooldown_until cleared)
```

---

### FallbackAttempt

A single routing event — one model tried, one outcome. Part of the request's routing history. Recorded as Phoenix span attributes and Prometheus counters.

| Field | Type | Values |
|---|---|---|
| `model_alias` | string | The model attempted |
| `attempt_number` | int | 1-based; per-model (resets for each new model in chain) |
| `failure_reason` | enum | `provider_error` \| `timeout` \| `context_overflow` \| `cooldown` |
| `outcome` | enum | `success` \| `failure` |

---

### RoutingDecision

The outcome of processing a request through the fallback chain. Surfaces in the API response (`model` field) and Phoenix span attributes.

| Field | Type | Description |
|---|---|---|
| `original_model_requested` | string | The model alias the caller specified |
| `fulfilled_by_model` | string | The model that produced the successful response (may differ from requested) |
| `total_attempts` | int | Count of all provider calls made across all models |
| `fallback_triggered` | bool | True if `fulfilled_by_model != original_model_requested` |
| `failure_reasons` | list[string] | Ordered list of failure reasons for each failed attempt |
| `outcome` | enum | `success` \| `exhausted` |

---

## Phoenix Span Attributes

Attributes set by `FallbackSpanCallback` on the LLM span when a fallback event occurs. Follow the OpenInference attribute naming convention already in use by the platform.

| Attribute | Type | Set When |
|---|---|---|
| `llm.fallback.triggered` | bool | Always set; `True` when a fallback occurred, `False` otherwise |
| `llm.fallback.reason` | string | Set when `llm.fallback.triggered == True`; one of `provider_error`, `timeout`, `context_overflow` |
| `llm.model.requested` | string | Always set; the model alias from the caller's request |
| `llm.fallback.attempt_count` | int | Set when `llm.fallback.triggered == True`; total number of provider attempts made |

**Constitution §2.4 compliance**: All four attributes are routing metadata. No prompt text, response text, or user-identifiable data is included.

---

## Fallback Chain Catalogue (Complete)

All 9 chat models with their error fallback chains and overflow targets:

| Primary Model | Error Fallback Chain | Context Overflow Target |
|---|---|---|
| `gpt-4o` | `claude-sonnet` → `gemini-pro` | `gpt-4.1` |
| `gpt-4o-mini` | `claude-haiku` → `gemini-flash` | `gpt-4.1` |
| `gpt-4.1` | `claude-sonnet` → `gemini-pro` | — (1 M context) |
| `o4-mini` | `claude-haiku` → `gemini-flash` | `gpt-4.1` |
| `claude-sonnet` | `gpt-4o` → `gemini-pro` | `gemini-pro` |
| `claude-haiku` | `gpt-4o-mini` → `gemini-flash` | `gemini-pro` |
| `gemini-pro` | `gpt-4o` → `claude-sonnet` | — (1 M context) |
| `gemini-flash` | `gpt-4o-mini` → `claude-haiku` | — (1 M context) |
| `command-r-plus` | `gpt-4o` → `claude-sonnet` | `gpt-4.1` |
| `text-embedding-3-small` | — (503 immediately) | — |
| `text-embedding-3-large` | — (503 immediately) | — |

---

## Routing Decision Flow

```
Request arrives at LiteLLM router
        │
        ▼
Is primary model in cooldown?
  YES → skip to first non-cooled fallback
  NO  → attempt primary model (up to 2 retries)
        │
        ├── SUCCESS → return response (llm.fallback.triggered=False)
        │
        └── FAILURE → increment consecutive_failures on primary
                      3 failures? → put primary in 60 s cooldown
                      advance to next model in chain
                        │
                        ├── Is fallback model in cooldown? → skip
                        ├── Attempt fallback (up to 2 retries)
                        │     ├── SUCCESS → return response (llm.fallback.triggered=True)
                        │     └── FAILURE → advance to next fallback
                        │
                        └── All fallbacks exhausted / all cooled down?
                              → HTTP 503 (all_fallbacks_exhausted)

Context overflow path:
  Token count > primary context_window?
    YES → treat as failure_reason=context_overflow
          route to context_window_fallbacks target
          target's own error fallback chain applies if target fails
```
