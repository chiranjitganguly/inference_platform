# Data Model: API Key Budget Enforcement & Spend Tracking

**Feature**: 009-key-budget-spend
**Date**: 2026-05-28

---

## Overview

All persistent state is owned by LiteLLM and stored in the `litellm` PostgreSQL database. This feature adds no new tables — it activates and extends LiteLLM's existing schema by ensuring `LITELLM_SALT_KEY` is present and `DATABASE_URL` is configured (both already present).

The portal-backend service is stateless; it aggregates from LiteLLM's admin API and holds no state of its own.

---

## LiteLLM Managed Tables

### `LiteLLM_VerificationToken`

Stores virtual keys and their associated budget configuration and running spend.

| Column | Type | Description |
|---|---|---|
| `token` | `VARCHAR` (PK) | Hashed key value using LITELLM_SALT_KEY |
| `key_alias` | `VARCHAR` (nullable) | Human-readable label for the key |
| `team_id` | `VARCHAR` (nullable) | Team this key belongs to |
| `max_budget` | `FLOAT` (nullable) | Monthly budget ceiling in USD. NULL = no ceiling. |
| `budget_duration` | `VARCHAR` (nullable) | Reset interval. `"monthly"` resets on 1st of each calendar month. |
| `budget_reset_at` | `TIMESTAMP` (nullable) | Next reset timestamp (UTC). Set by LiteLLM on key creation. |
| `spend` | `FLOAT` | Accumulated USD spend in the current budget period. |
| `models` | `TEXT[]` (nullable) | Model allowlist. NULL = all models allowed. |
| `expires` | `TIMESTAMP` (nullable) | Key expiry. NULL = never expires. |
| `created_at` | `TIMESTAMP` | Creation timestamp (UTC). |
| `updated_at` | `TIMESTAMP` | Last updated timestamp (UTC). |

**Key constraints**:
- `spend` is incremented atomically by LiteLLM after each completed request.
- `max_budget` is compared against `spend` at the start of every request (before forwarding to upstream model).
- When `spend >= max_budget`, LiteLLM returns HTTP 429 immediately.
- `budget_reset_at` is set to the first day of the next calendar month (00:00:00 UTC) when a key is created with `budget_duration: monthly`.
- On reset, LiteLLM sets `spend = 0` and advances `budget_reset_at` by one month.

---

### `LiteLLM_SpendLogs`

Immutable per-request spend record appended after each successful inference call.

| Column | Type | Description |
|---|---|---|
| `request_id` | `VARCHAR` (PK) | `X-Request-ID` value from Kong correlation-id plugin. |
| `api_key` | `VARCHAR` | Hashed virtual key (`LiteLLM_VerificationToken.token`). |
| `model` | `VARCHAR` | Canonical model name used for the request (e.g., `gpt-4o-mini`). |
| `model_group` | `VARCHAR` | Model group (e.g., `gpt-4o-mini`). May differ from `model` after fallback. |
| `prompt_tokens` | `INTEGER` | Tokens in the prompt sent to the provider. |
| `completion_tokens` | `INTEGER` | Tokens in the completion returned by the provider. |
| `total_tokens` | `INTEGER` | `prompt_tokens + completion_tokens`. |
| `spend` | `FLOAT` | USD cost: `(prompt_tokens × input_rate + completion_tokens × output_rate) / 1,000,000`. |
| `startTime` | `TIMESTAMP` | Request start timestamp (UTC). |
| `endTime` | `TIMESTAMP` | Response completion timestamp (UTC). |
| `cache_hit` | `BOOLEAN` | Whether response was served from Redis cache. |
| `metadata` | `JSONB` (nullable) | Arbitrary metadata from the request, including Langfuse prompt fields. |

**Prompt content constraint**: The `metadata` JSONB field stores request metadata only — never prompt text or response text. This satisfies Constitution Principle II.

**Langfuse linkage**: When a request carries `metadata.langfuse_prompt_name` and `metadata.langfuse_prompt_version`, these values appear in `LiteLLM_SpendLogs.metadata` and are forwarded to the Langfuse trace by the `langfuse` callback. The Langfuse trace receives `cost`, `usage.prompt_tokens`, `usage.completion_tokens`, `model`, `langfuse_prompt_name`, and `langfuse_prompt_version` as top-level trace attributes.

---

## Portal-Backend Response Shape

The `GET /v1/spend` endpoint produced by portal-backend aggregates LiteLLM's admin APIs into this shape:

```json
{
  "period_start": "2026-05-01T00:00:00Z",
  "period_end": "2026-05-31T23:59:59Z",
  "total_spend_usd": 12.847,
  "by_model": [
    {
      "model": "gpt-4o-mini",
      "spend_usd": 8.12,
      "prompt_tokens": 1820000,
      "completion_tokens": 245000
    },
    {
      "model": "claude-haiku",
      "spend_usd": 4.727,
      "prompt_tokens": 980000,
      "completion_tokens": 118000
    }
  ],
  "by_key": [
    {
      "key_alias": "team-alpha",
      "key_hash": "sk-...masked...",
      "spend_usd": 9.341,
      "max_budget_usd": 20.0,
      "budget_remaining_usd": 10.659,
      "budget_reset_at": "2026-06-01T00:00:00Z"
    },
    {
      "key_alias": "team-beta",
      "key_hash": "sk-...masked...",
      "spend_usd": 3.506,
      "max_budget_usd": null,
      "budget_remaining_usd": null,
      "budget_reset_at": null
    }
  ]
}
```

**Field semantics**:
- `total_spend_usd` — sum of all `by_model[].spend_usd` values.
- `by_model` — sourced from LiteLLM's `GET /global/spend/models`, filtered to current calendar month.
- `by_key` — sourced from LiteLLM's `GET /global/spend/keys`, current month spend only.
- `budget_remaining_usd` — `max_budget_usd - spend_usd`; `null` when no ceiling is set.
- `key_hash` — last 8 characters of the hashed key are shown; full hash never exposed.

---

## State Transitions: Virtual Key Budget

```
Key Created (spend=0)
       │
       ▼
  Request Arrives
       │
       ├─ spend < max_budget ──► Forward to upstream ──► spend += cost ──► SpendLog appended
       │
       └─ spend >= max_budget ──► HTTP 429 budget_exceeded (nothing forwarded)

  Spend Store Unavailable ──► HTTP 503 spend_store_unavailable (nothing forwarded)

  First of month (00:00 UTC) ──► spend = 0, budget_reset_at += 1 month
```

---

## Cost Calculation Formula

```
cost_usd = (prompt_tokens × input_rate_per_million + completion_tokens × output_rate_per_million) / 1,000,000
```

Rates are sourced from LiteLLM's built-in provider pricing tables (bundled in the LiteLLM library at `ghcr.io/berriai/litellm:main-v1.52.0`). Operators do not maintain a separate rate table for this version.
