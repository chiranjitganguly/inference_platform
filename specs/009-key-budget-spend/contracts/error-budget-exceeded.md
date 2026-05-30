# Contract: HTTP 429 budget_exceeded

**Produced by**: LiteLLM (native enforcement)
**Trigger**: Inference request received from a virtual key whose `spend >= max_budget`.
**Guaranteed**: Request is NOT forwarded to upstream model provider.

---

## Response

```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/json
X-Request-ID: <uuid>
X-Platform: inference-platform
X-API-Version: 1
```

```json
{
  "error": {
    "message": "Budget has been exceeded! Current spend: 20.001 USD; Max Budget: 20.0 USD",
    "type": "budget_exceeded",
    "code": 429
  }
}
```

**Field semantics**:
| Field | Description |
|---|---|
| `error.message` | Human-readable description including current spend and max budget values |
| `error.type` | Always `budget_exceeded` for this error class |
| `error.code` | Always `429` |

**Notes**:
- Response headers `X-Request-ID`, `X-Platform`, and `X-API-Version` are always attached by Kong's global plugins regardless of LiteLLM error responses.
- The error shape is OpenAI-compatible (matches the `{ "error": { "message", "type", "code" } }` schema).
- Callers should not retry with the same key — the key is blocked until the monthly reset date (`budget_reset_at`).
- The reset date is visible on `GET /v1/spend` in `by_key[].budget_reset_at`.

---

## Contract: HTTP 503 spend_store_unavailable

**Produced by**: LiteLLM (when PostgreSQL is unreachable during budget check) OR portal-backend (when LiteLLM admin API is unreachable).

```http
HTTP/1.1 503 Service Unavailable
Content-Type: application/json
```

```json
{
  "error": "spend_store_unavailable",
  "message": "Budget check could not be completed. Request rejected.",
  "detail": {}
}
```

**Notes**:
- Fail-closed: no request is forwarded to the upstream model when the spend store is unreachable.
- This satisfies FR-012 and the clarification from the 2026-05-28 session.
