# Research: Chat Completion with Observability

**Feature**: `005-chat-completion-otel-langfuse` | **Date**: 2026-05-28

## Decision 1 — LiteLLM Metadata Forwarding to Callbacks

**Decision**: Use LiteLLM's native `metadata` passthrough in the request body. Fields set in `metadata` by the caller are available inside callback functions as `kwargs["litellm_params"]["metadata"]`.

**Rationale**: LiteLLM propagates the full `metadata` dict from the API request body into every callback invocation. No custom middleware or config extension is needed. `arize_phoenix` picks up `metadata.request_id` and `metadata.team` as span tags automatically when tagged via `litellm_settings.default_team_settings` or passed through the callback's `kwargs`. Langfuse receives metadata fields via the `langfuse` callback's `trace_metadata` hook, which reads `kwargs["litellm_params"]["metadata"]`.

**Alternatives considered**:
- Custom FastAPI middleware wrapping LiteLLM — rejected: adds unnecessary service layer; callbacks already handle this.
- Separate OTel collector pipeline with manual span creation — rejected: spec explicitly states "no custom instrumentation code."

---

## Decision 2 — Langfuse Prompt Linking Mechanism

**Decision**: Pass `metadata.prompt_name` through LiteLLM's metadata dict; the `langfuse` callback calls `langfuse.get_prompt(name, label="production")` internally when `prompt_name` is present in `kwargs["litellm_params"]["metadata"]`.

**Rationale**: LiteLLM's built-in Langfuse callback supports `prompt_name` as a first-class metadata key (documented in LiteLLM v1.52 callback config). When present, it calls Langfuse's prompt fetch and attaches the `production` label version to the trace. When absent, a trace is still created without prompt linkage.

**Alternatives considered**:
- Fetch prompt in a pre-request hook and inject system message — rejected: changes request content (violates prompt ephemerality principle) and is custom code.
- Store prompt version ID in each model's config — rejected: ties prompt versions to model config, preventing independent rotation.

---

## Decision 3 — Model Validation: LiteLLM Native 400 vs. Pre-validation

**Decision**: Rely on LiteLLM's built-in model validation (HTTP 400 with `{"error": {"message": "...model not found...", "type": "invalid_request_error", "code": 400}}`) without adding a separate pre-validation layer.

**Rationale**: LiteLLM checks the requested model name against its `model_list` at the start of request handling, before making any upstream API call. This satisfies SC-004 (p99 < 50 ms for 400 errors — no upstream call). The error body includes the model name, satisfying FR-003's requirement for a structured error body naming the invalid model.

**Alternatives considered**:
- Pre-validation in a Kong plugin — rejected: requires Lua scripting and synchronising the model list with kong.yml; high maintenance burden.
- Pre-validation in a Guardrails check — rejected: Guardrails does not exist yet (ordered exception documented in plan).

---

## Decision 4 — Phoenix + Langfuse Docker Compose Service Configuration

**Decision**: Add Phoenix and Langfuse to `docker-compose.yml` under the `obs` profile. Both depend on postgres (healthcheck). Phoenix uses `arizephoenix/phoenix:latest` (constitution §3 exception); Langfuse uses `langfuse/langfuse:3`.

**Rationale**: The `obs` profile already conceptually includes observability sinks. Pinning Phoenix to `:latest` is a documented constitution exception. Langfuse 3 is the stable series with the callback API LiteLLM v1.52 targets. Both databases (`phoenix` and `langfuse`) are already created by `scripts/init-db.sql`.

**Service configuration**:
- **Phoenix Arize**: port `6006` (OTLP HTTP endpoint), environment `PHOENIX_SQL_DATABASE_URL`, no host port binding except for development inspection.
- **Langfuse server**: port `3002` (REST + OTLP HTTP), environment `DATABASE_URL`, `NEXTAUTH_URL`, `NEXTAUTH_SECRET`, `SALT`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`.
- **Langfuse worker**: separate container `langfuse/langfuse-worker:3` consuming the same database for async processing.

**LiteLLM env vars** already present in docker-compose.yml (from feature 002):
- `OTEL_EXPORTER_OTLP_ENDPOINT=http://arize-phoenix:6006/v1/traces`
- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`

**Alternatives considered**:
- Managed Phoenix Cloud — rejected: not available in local dev; requires external network; violates local-first principle.
- Single combined Langfuse + worker image — rejected: Langfuse 3 publishes separate `langfuse` and `langfuse-worker` images by design.

---

## Decision 5 — Observability Sink Graceful Degradation

**Decision**: No config change needed. LiteLLM's callback system is fire-and-forget by default: if a callback raises an exception (e.g., Phoenix unreachable), LiteLLM logs the error internally and continues returning the inference response to the caller.

**Rationale**: LiteLLM v1.52 wraps all callback invocations in try/except. The response is assembled and sent before callbacks fire (post-processing hooks). This natively satisfies FR-011 without any custom error handling.

**Verification**: The LiteLLM source for `v1.52` confirms callbacks are invoked in `litellm/integrations/` via `asyncio.ensure_future()` — they are non-blocking relative to the response path.

**Alternatives considered**:
- Circuit breaker around callback calls — rejected: LiteLLM already provides this behavior; adding another layer is redundant complexity.
