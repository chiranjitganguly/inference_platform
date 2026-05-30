# Research: API Key Budget Enforcement & Spend Tracking

**Feature**: 009-key-budget-spend
**Date**: 2026-05-28

---

## Decision 1 — Virtual Key Budget Engine

**Decision**: Use LiteLLM's native virtual key system with `max_budget` and `budget_duration: monthly`.

**Rationale**: LiteLLM v1.52.0 has built-in budget enforcement. When `DATABASE_URL` is configured (already done in `docker-compose.yml`), LiteLLM automatically:
- Creates `LiteLLM_SpendLogs` and `LiteLLM_VerificationToken` tables in PostgreSQL.
- Accumulates `spend` on `LiteLLM_VerificationToken` after each request completion.
- Rejects requests where `key.spend >= key.max_budget` with HTTP 429 and error code `budget_exceeded`.
- Resets `spend` to `0` on the first of each calendar month when `budget_duration: monthly`.
- Requires `LITELLM_SALT_KEY` environment variable for key hashing/verification.

**Alternatives considered**:
- Custom PostgreSQL trigger on spend accumulation: rejected — duplicates logic LiteLLM already provides correctly.
- Redis-based atomic spend counter: rejected — LiteLLM's DB-backed approach is authoritative; Redis would require reconciliation logic.

---

## Decision 2 — Spend Report Endpoint

**Decision**: Build a minimal FastAPI endpoint in `services/portal-backend/main.py` that aggregates LiteLLM's `/global/spend/keys` and `/global/spend/models` admin APIs into the target shape `{ total_spend_usd, by_model[], by_key[] }`. Expose via Kong at `GET /v1/spend`.

**Rationale**: LiteLLM's native spend endpoints (`/spend/logs`, `/global/spend/keys`, `/global/spend/models`) return different response shapes and require separate calls. A thin aggregation layer in portal-backend produces the exact shape the spec requires in a single client call. Portal-backend is already defined in CLAUDE.md as the "Cost estimator + catalogue API" at port 8092.

**LiteLLM spend endpoints used** (confirmed in LiteLLM v1.52.x admin API):
- `GET /global/spend/keys` — returns `[{ api_key, spend, max_budget, budget_duration, ... }]`
- `GET /global/spend/models` — returns `[{ model, spend, ... }]`

Both require `Authorization: Bearer <master_key>` header. Portal-backend forwards the caller's Authorization header verbatim — LiteLLM validates master key authority.

**Alternatives considered**:
- Route `/v1/spend` directly to LiteLLM: rejected — LiteLLM's native `/global/spend/keys` and `/global/spend/models` are separate endpoints; Kong cannot merge two upstream responses. Client would need two calls with different paths.
- Query the `LiteLLM_SpendLogs` PostgreSQL table directly from portal-backend: rejected — bypasses LiteLLM's access control; creates a direct DB dependency outside LiteLLM's schema contract.

---

## Decision 3 — Langfuse Cost Metadata

**Decision**: No code changes required. LiteLLM's `langfuse` callback already sends `cost`, `prompt_tokens`, `completion_tokens`, and `model` as trace metadata to Langfuse when `LANGFUSE_HOST` is set.

**Rationale**: `LANGFUSE_HOST: ${LANGFUSE_HOST}` is already present in the `litellm` service environment in `docker-compose.yml`. The `langfuse` callback is already listed in `litellm_settings.callbacks`. When a request carries `metadata.langfuse_prompt_name` and `metadata.langfuse_prompt_version`, LiteLLM's callback passes these through automatically.

**Verification**: After running `make up-obs`, issue a chat completion with `metadata` fields and inspect the Langfuse trace — `cost`, `usage.prompt_tokens`, `usage.completion_tokens` appear automatically.

---

## Decision 4 — Kong Routing for Admin Endpoints

**Decision**: Add two new Kong service definitions:
1. **litellm-admin** — proxies `/v1/key/*` → LiteLLM `/key/*`. No Kong key-auth plugin; LiteLLM enforces master key.
2. **portal-backend** — proxies `/v1/spend` → portal-backend `/v1/spend`. No Kong key-auth plugin; portal-backend proxies auth to LiteLLM.

**Rationale**: The existing `/v1` litellm-proxy route matches all paths under `/v1` — it would match `/v1/key/generate` and forward it correctly if path-stripping is not enabled. However, the existing litellm-proxy route has `strip_path: false`, meaning Kong forwards `/v1/key/generate` to LiteLLM at the path `/v1/key/generate`, which LiteLLM does NOT handle (LiteLLM mounts key endpoints at `/key/generate`, not `/v1/key/generate`).

Therefore an explicit litellm-admin service is needed to handle `/v1/key/*` → LiteLLM `/key/*` with path rewriting. Kong's route `strip_path: true` plus a service URL to `http://litellm:4000` achieves this: `/v1/key/generate` → `/key/generate`.

**Alternatives considered**:
- Mount key endpoints at `/key/*` without `/v1` prefix: rejected — requires bypassing Kong's host port, violating Principle I.
- Add key management to the existing litellm service with a separate route: compatible approach but requires path rewriting that the existing service does not need.

---

## Decision 5 — Fail Closed on Spend Store Unavailability

**Decision**: The spend store (PostgreSQL) is an explicit `depends_on` healthcheck dependency for LiteLLM in `docker-compose.yml`. LiteLLM cannot start if PostgreSQL is unhealthy. If PostgreSQL becomes unavailable after startup, LiteLLM's spend check will fail and LiteLLM will return a 5xx error — requests are not forwarded to upstream models. This is inherently fail-closed.

For the portal-backend `/v1/spend` endpoint: if LiteLLM's admin API is unreachable, portal-backend returns HTTP 503 `spend_store_unavailable`.

**Rationale**: Relies on Docker Compose's healthcheck dependency to ensure the spend store is always present when LiteLLM starts. No additional code needed for the primary enforcement path.

---

## Decision 6 — LITELLM_SALT_KEY Requirement

**Decision**: Add `LITELLM_SALT_KEY: ${LITELLM_SALT_KEY}` to the litellm service environment in `docker-compose.yml`. `LITELLM_SALT_KEY` is already defined in `.env.example`.

**Rationale**: LiteLLM requires `LITELLM_SALT_KEY` to deterministically hash virtual key values before storing them in `LiteLLM_VerificationToken.token`. Without it, virtual key verification will silently fail or fall back to an insecure default. The variable is already in `.env.example` but was not wired into the docker-compose service environment.

---

## No-Change Confirmations

| Area | Status | Evidence |
|---|---|---|
| PostgreSQL `litellm` database | Ready | `DATABASE_URL` already in docker-compose litellm env; `init-db.sql` creates the database |
| Langfuse callback + LANGFUSE_HOST | Ready | Both present in docker-compose litellm env |
| LiteLLM callbacks: arize_phoenix, langfuse, prometheus | Ready | Already listed in `litellm_settings.callbacks` |
| Monthly budget reset logic | Ready | LiteLLM native — `budget_duration: monthly` on key |
| 429 + budget_exceeded error | Ready | LiteLLM native — returned automatically on budget exhaustion |
| Cost formula | Ready | LiteLLM uses provider-published pricing tables built into the library |
