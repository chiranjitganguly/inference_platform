# Research: Embeddings Endpoint (011)

**Branch**: `011-embeddings-endpoint` | **Date**: 2026-05-30

## 1. LiteLLM Model Type Enforcement (FR-003)

**Decision**: Model type validation is handled natively by LiteLLM — no custom enforcement layer required.

**Rationale**: When `/v1/embeddings` receives a model whose `model_info.type` is not `embedding`, LiteLLM returns `400 BadRequestError: This model doesn't have embedding support` before making any upstream API call. The `type: embedding` field already present in `config.yaml` for both embedding models is the authoritative signal LiteLLM uses to populate its internal `mode` field, which drives endpoint-level validation.

**Alternatives considered**:
- Kong-level model name allowlist: rejected — Kong has no awareness of the LiteLLM model catalogue; maintaining a parallel list creates drift risk.
- Guardrails pre-validation: rejected — Guardrails is not yet in the chain (Phase 03); adds a blocking dependency on the safety profile for a core feature.

---

## 2. Cache Bypass (FR-006)

**Decision**: Remove `aembedding` and `embedding` from `litellm_settings.cache_params.supported_call_types` in `services/litellm/config.yaml`.

**Rationale**: LiteLLM's cache only activates for call types listed in `supported_call_types`. Currently the config explicitly includes `aembedding` and `embedding` — removing them disables caching for all embedding calls globally and permanently, with zero per-request logic required. This is the correct mechanism for a categorical "never cache embeddings" requirement.

**Alternatives considered**:
- Per-request `Cache-Control: no-store` header from Guardrails: rejected — fragile, only works when Guardrails is in the chain, and relies on correct header forwarding.
- Redis key prefix exclusion pattern: rejected — LiteLLM does not expose a cache key filter at config level in v1.52.0.

---

## 3. Callback Suppression for Phoenix / Langfuse (FR-008)

**Decision**: When callbacks are re-enabled (obs profile), the Guardrails service will inject `metadata: {"no_log": "True"}` into embedding requests forwarded to LiteLLM.

**Rationale**: LiteLLM v1.52.0 respects `metadata.no_log: "True"` at request level to skip the `arize_phoenix` and `langfuse` success callbacks. LiteLLM's internal Prometheus instrumentation (`/metrics` endpoint) is populated by a separate in-process counter that runs regardless of the success callback chain — so Prometheus metrics are preserved even when `no_log` is set.

**Note on current state**: The global callbacks line in `config.yaml` is currently commented out for core-profile dev. This mechanism becomes active when the line is uncommented for obs-profile operation. The Guardrails embedding path handler must inject `no_log` unconditionally so the behaviour is correct in both states.

**Alternatives considered**:
- Per-model `success_callback: [prometheus]` in config.yaml YAML: not supported in LiteLLM v1.52.0 YAML spec — `success_callback` is a global `litellm_settings` key only.
- Separate LiteLLM instance for embeddings: rejected — adds a new service, violates the "no new services without ADR" constraint, and contradicts the single-proxy architecture.

---

## 4. Kong Route Strategy (FR-009 + batch timeout)

**Decision**: Add a dedicated `/v1/embeddings` route in `services/kong/kong.yml` pointing to `http://litellm:4000` with `read_timeout: 120000` and `write_timeout: 120000`.

**Rationale**: The existing `litellm-proxy` route (`/v1`) has `read_timeout: 60000`. Large batch embedding requests (up to 100 inputs of dense text) can exceed 60 seconds. Kong's most-specific-path-wins matching ensures the dedicated `/v1/embeddings` route takes priority over the `/v1` catch-all. The route reuses the same `key-auth` plugin configuration as chat routes — no new auth mechanism required.

**Alternatives considered**:
- Rely on the existing `/v1` catch-all: rejected — 60s timeout is insufficient for large batches; no way to tune without affecting all `/v1` traffic.
- Separate Kong service entry: technically equivalent but requires duplicate service config; a route-level override on the existing service is cleaner.

---

## 5. BYOK Key Resolution

**Decision**: Both embedding models use `api_key: os.environ/OPENAI_API_KEY` — the same BYOK key already used for `gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, and `o4-mini`.

**Rationale**: OpenAI embedding models and chat models share the same API key namespace. No additional Vault secret or env var is needed.

---

## 6. Loki Audit Logging

**Decision**: Embedding requests produce a Loki audit entry (metadata only) via the same LiteLLM `loki` callback path used by chat completions, when the obs profile is active.

**Rationale**: The constitution requires an audit trail for all requests transiting Kong (§6.3). Exempting embedding calls would create a compliance gap in spend accountability and access auditing. The `no_log: True` metadata flag suppresses Phoenix/Langfuse specifically; it does not suppress Loki structured logging. Loki entries contain only: `timestamp`, `event_type`, `request_id`, `key_hash`, `model_name`, `token_count` — no vector content, no input text.

---

## 7. Encoding Format

**Decision**: Only `encoding_format: float` is supported. `base64` is out of scope.

**Rationale**: Confirmed in spec Assumptions. LiteLLM defaults to `float` when `encoding_format` is omitted. Callers may explicitly pass `encoding_format: float` or omit the field entirely — both produce identical behaviour. Passing `encoding_format: base64` will result in a LiteLLM passthrough to OpenAI, which will return base64 — this is acceptable as an undocumented passthrough; the platform makes no guarantee about it.
