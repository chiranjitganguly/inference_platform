# Contract: GET /v1/spend

**Service**: portal-backend (via Kong)
**Effective path**: Kong `/v1/spend` → portal-backend `:8092/v1/spend` → LiteLLM admin API
**Auth**: `Authorization: Bearer <master_key>` (forwarded to LiteLLM for validation)
**Profile required**: `core` + portal-backend container running

---

## Request

```http
GET /v1/spend HTTP/1.1
Host: localhost:8080
Authorization: Bearer <LITELLM_MASTER_KEY>
```

**Optional query parameters**:
| Parameter | Type | Description |
|---|---|---|
| `key_id` | string | Filter `by_key[]` to a single key alias or key hash. |

---

## Response — 200 OK

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
    }
  ],
  "by_key": [
    {
      "key_alias": "team-alpha",
      "key_hash": "sk-...pha4",
      "spend_usd": 9.341,
      "max_budget_usd": 20.0,
      "budget_remaining_usd": 10.659,
      "budget_reset_at": "2026-06-01T00:00:00Z"
    }
  ]
}
```

**Field semantics**:
| Field | Type | Source |
|---|---|---|
| `period_start` | ISO-8601 UTC | First day of current calendar month, 00:00:00 UTC |
| `period_end` | ISO-8601 UTC | Last day of current calendar month, 23:59:59 UTC |
| `total_spend_usd` | float | Sum of all `by_model[].spend_usd` |
| `by_model[].model` | string | Canonical model name from LiteLLM |
| `by_model[].spend_usd` | float | Total spend for this model in current period |
| `by_model[].prompt_tokens` | integer | Total prompt tokens for this model in current period |
| `by_model[].completion_tokens` | integer | Total completion tokens for this model in current period |
| `by_key[].key_alias` | string/null | `key_alias` from `LiteLLM_VerificationToken` |
| `by_key[].key_hash` | string | Last 4 chars of hashed token, prefixed `sk-...` |
| `by_key[].spend_usd` | float | Accumulated spend this period |
| `by_key[].max_budget_usd` | float/null | Budget ceiling; `null` if no ceiling |
| `by_key[].budget_remaining_usd` | float/null | `max_budget_usd - spend_usd`; `null` if no ceiling |
| `by_key[].budget_reset_at` | ISO-8601 UTC/null | Next reset timestamp; `null` if no budget configured |

---

## Response — 401 Unauthorized

```json
{
  "error": "unauthorized",
  "message": "Master key required to access spend report",
  "detail": {}
}
```

**Trigger**: `Authorization` header absent or key is not the LiteLLM master key.

---

## Response — 503 Service Unavailable

```json
{
  "error": "spend_store_unavailable",
  "message": "Spend data temporarily unavailable",
  "detail": {}
}
```

**Trigger**: portal-backend cannot reach LiteLLM's admin API (fail-closed behaviour per spec FR-012).

---

## Internal Aggregation Logic (portal-backend)

1. Forward `Authorization` header to `GET http://litellm:4000/global/spend/keys`.
2. Forward `Authorization` header to `GET http://litellm:4000/global/spend/models`.
3. If either call returns non-200: return HTTP 503 `spend_store_unavailable`.
4. Compute `total_spend_usd = sum(by_model[].spend_usd)`.
5. Mask `key_hash`: show only `sk-...{last4chars}`.
6. Compute `budget_remaining_usd` where `max_budget_usd` is non-null.
7. Populate `period_start`/`period_end` from current UTC calendar month.
8. If `key_id` query parameter is set, filter `by_key[]` by `key_alias` or key hash match.
