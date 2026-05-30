# Contract: POST /v1/key/generate

**Service**: LiteLLM (via Kong)
**Effective path**: Kong `/v1/key/generate` → LiteLLM `/key/generate`
**Auth**: `Authorization: Bearer <master_key>` (LiteLLM enforces master key)
**Profile required**: `core`

---

## Request

```http
POST /v1/key/generate HTTP/1.1
Host: localhost:8080
Authorization: Bearer <LITELLM_MASTER_KEY>
Content-Type: application/json
```

```json
{
  "key_alias": "team-alpha",
  "max_budget": 20.00,
  "budget_duration": "monthly",
  "models": ["gpt-4o-mini", "claude-haiku", "gemini-flash"],
  "duration": null
}
```

**Field rules**:
| Field | Required | Type | Description |
|---|---|---|---|
| `key_alias` | No | string | Human-readable label. Shown in spend report `by_key[].key_alias`. |
| `max_budget` | No | float | Monthly budget ceiling in USD. Omit or set to `null` for unlimited. |
| `budget_duration` | No | string | Must be `"monthly"` for calendar-month reset. |
| `models` | No | string[] | Model allowlist. Omit for all models. |
| `duration` | No | string/null | Key validity window (e.g., `"30d"`). `null` = never expires. |

---

## Response — 200 OK

```json
{
  "key": "sk-...",
  "key_name": "team-alpha",
  "expires": null,
  "max_budget": 20.00,
  "budget_duration": "monthly",
  "budget_reset_at": "2026-06-01T00:00:00Z",
  "models": ["gpt-4o-mini", "claude-haiku", "gemini-flash"],
  "token": "<hashed-token>"
}
```

**Notes**:
- `key` — the plaintext key value. Shown once at creation; not recoverable after this response.
- `budget_reset_at` — UTC timestamp of the next monthly reset (always 1st of next month, 00:00:00 UTC).
- Clients pass `key` as `Authorization: Bearer <key>` in all inference requests.

---

## Response — 401 Unauthorized

```json
{
  "error": "unauthorized",
  "message": "Master key required for key generation",
  "detail": {}
}
```

**Trigger**: `Authorization` header absent, invalid, or not the master key.

---

## Response — 400 Bad Request

```json
{
  "error": "bad_request",
  "message": "budget_duration must be one of: daily, weekly, monthly",
  "detail": { "field": "budget_duration", "value": "quarterly" }
}
```

**Trigger**: Unrecognised `budget_duration` value.
