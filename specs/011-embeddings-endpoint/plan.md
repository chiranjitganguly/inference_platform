# Implementation Plan: Embeddings Endpoint

**Branch**: `011-embeddings-endpoint` | **Date**: 2026-05-30 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/011-embeddings-endpoint/spec.md`

## Summary

Activate the embeddings endpoint by (1) removing embedding call types from the LiteLLM cache to satisfy the no-cache requirement, (2) adding a dedicated Kong route for `/v1/embeddings` with appropriate timeouts for batch operations, and (3) extending the Guardrails service to inject `no_log` metadata on embedding requests so Phoenix/Langfuse are bypassed when callbacks are active. Both embedding models (`text-embedding-3-small` at 1536 dims and `text-embedding-3-large` at 3072 dims) and their `type: embedding` catalogue entries already exist in `config.yaml`; no new model declarations are needed.

## Technical Context

**Language/Version**: Python 3.11 (Guardrails service), YAML (LiteLLM config), declarative JSON/YAML (Kong config)

**Primary Dependencies**: LiteLLM main-v1.52.0, Kong 3.6, Redis 7.2-alpine (cache), FastAPI + httpx (Guardrails)

**Storage**: No new storage. Existing `litellm_spendlogs` table in the `litellm` PostgreSQL database captures embedding spend automatically.

**Testing**: `make smoke` (curl-based); `pytest` for Guardrails unit tests; `ruff` + `mypy --strict` for Python quality gates

**Target Platform**: Linux/Docker Compose (linux/amd64 + linux/arm64)

**Project Type**: Configuration-driven modification + minor Python service extension

**Performance Goals**: Single embedding request ≤ 3 seconds; batch of 100 inputs ≤ 120 seconds (Kong timeout set accordingly)

**Constraints**: No caching (FR-006); no Phoenix/Langfuse callbacks (FR-008); must work on `core` profile alone; must not add new services

**Scale/Scope**: 2 embedding models, batch up to 100 inputs per request; BYOK via existing `OPENAI_API_KEY`

## Constitution Check

| Principle | Status | Notes |
|---|---|---|
| I. Request Flow Integrity | ✅ Pass | `/v1/embeddings` follows existing Kong → LiteLLM direct pattern; Guardrails not yet in chain (Phase 03 pre-existing decision — see kong.yml comment on `litellm-proxy` route) |
| II. Prompt Content Ephemeral | ✅ Pass | Input text is consumed for vectorisation only; float array response has no text content; Loki entries are metadata-only |
| III. OpenAI API Compatibility | ✅ Pass | FR-001 mandates OpenAI `/v1/embeddings` schema; LiteLLM implements it natively |
| IV. Defence in Depth | ✅ Pass | Kong key-auth covers the new route; OPA ABAC applies; Guardrails stub proxies all traffic |
| V. Falsifiable Acceptance Criteria | ✅ Pass | All SC are `curl`-verifiable with `jq` assertions or Prometheus queries |

No violations. No Complexity Tracking entries required.

## Project Structure

### Documentation (this feature)

```text
specs/011-embeddings-endpoint/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions + rationale
├── data-model.md        # Wire schemas + affected storage
├── quickstart.md        # Curl-based verification guide
├── contracts/
│   └── embeddings-api.md   # Full API contract (request, response, errors, guarantees)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (files touched by this feature)

```text
services/
├── litellm/
│   └── config.yaml              # Remove aembedding + embedding from cache supported_call_types
└── kong/
    └── kong.yml                 # Add /v1/embeddings route with 120s timeouts + key-auth

scripts/
└── smoke-test.sh                # Add embedding test cases (small, large, chat-model-rejection, batch)
```

The Guardrails service (`services/guardrails/main.py`) requires a targeted extension for FR-008 (no Phoenix/Langfuse). The current stub proxies all traffic blindly — it must detect embedding requests and inject `metadata.no_log: "True"` before forwarding to LiteLLM. This is a minimal, contained change: one path-detection check on `POST /v1/embeddings` in the existing `proxy()` function.

## Change Details

### 1. `services/litellm/config.yaml` — Remove embedding from cache supported_call_types

**Current state** (lines 188–192):
```yaml
    supported_call_types:
      - acompletion
      - completion
      - aembedding
      - embedding
```

**Target state**:
```yaml
    supported_call_types:
      - acompletion
      - completion
```

**Why**: `aembedding` and `embedding` in `supported_call_types` cause LiteLLM to read from and write to Redis for every embedding call. Removing them disables embedding caching at the config level — no per-request logic needed. This is the authoritative mechanism in LiteLLM v1.52.0.

---

### 2. `services/kong/kong.yml` — Add dedicated `/v1/embeddings` route

Insert before the existing `litellm-proxy` route (more specific path must appear first for Kong priority matching):

```yaml
      # Embeddings endpoint — longer timeouts for batch operations (FR-011-009)
      - name: litellm-embeddings
        paths:
          - /v1/embeddings
        methods:
          - POST
        strip_path: false
        plugins:
          - name: key-auth
            config:
              key_names:
                - Authorization
              key_in_header: true
              hide_credentials: true
```

**Parent service**: attach to the existing `litellm` service entry (url: `http://litellm:4000`) but override timeouts at the service level — Kong 3.6 does not support per-route timeout overrides in declarative config. The `litellm` service needs its timeouts raised to 120s, or a new service entry `litellm-embeddings` is added with `url: http://litellm:4000` and `read_timeout: 120000`.

**Resolved approach**: Add a second service entry `litellm-embeddings` pointing to the same upstream with extended timeouts. This avoids changing the timeouts on the shared `litellm` service (which would affect all chat routes).

```yaml
  - name: litellm-embeddings
    url: http://litellm:4000
    connect_timeout: 10000
    read_timeout:    120000
    write_timeout:   120000

    routes:
      - name: litellm-embeddings-route
        paths:
          - /v1/embeddings
        methods:
          - POST
        strip_path: false
        plugins:
          - name: key-auth
            config:
              key_names:
                - Authorization
              key_in_header: true
              hide_credentials: true
```

---

### 3. `services/guardrails/main.py` — Inject `no_log` for embedding requests

The existing catch-all `proxy()` function must be extended to detect embedding requests and inject the `no_log` metadata field into the forwarded request body.

**Insertion point**: Before the `httpx.AsyncClient.request()` call in `proxy()`, add:

```python
if path == "v1/embeddings" and request.method == "POST":
    body = _inject_no_log(body)
```

**New helper function**:

```python
def _inject_no_log(body: bytes) -> bytes:
    """Inject no_log=True so LiteLLM skips Phoenix/Langfuse callbacks for embeddings."""
    try:
        payload: dict = json.loads(body)
    except Exception:
        return body
    payload.setdefault("metadata", {})["no_log"] = "True"
    return json.dumps(payload).encode()
```

**Scope**: This function is intentionally narrow. It only modifies embedding requests and leaves all other paths untouched. It is safe to apply even when callbacks are disabled (the metadata field is ignored by LiteLLM when no callbacks are configured).

---

### 4. `scripts/smoke-test.sh` — Add embedding test cases

Add four embedding assertions to the existing smoke test suite:

1. `text-embedding-3-small` returns 1536-element array
2. `text-embedding-3-large` returns 3072-element array
3. Chat model (`gpt-4o`) on `/v1/embeddings` returns HTTP 400
4. Batch of 3 inputs returns 3 embedding objects

---

## Acceptance Verification

All acceptance criteria from `spec.md` are verifiable via commands in `quickstart.md`. Falsifiable test for each SC:

| SC | Verification command |
|---|---|
| SC-001: ≤3s single embedding | `time curl ... text-embedding-3-small` → wall time |
| SC-002: 100% chat model rejection | `curl ... gpt-4o → HTTP 400` |
| SC-003: Token usage present | `jq '.usage.total_tokens > 0'` on every response |
| SC-004: Batch ordering preserved | `jq '.data\|length == 3 and .data[0].index == 0'` |
| SC-005: No Phoenix/Langfuse traces | Query Phoenix `/v1/spans` after 50 embedding calls → 0 embedding spans |
| SC-006: No caching | Two identical requests → 2 entries in `litellm_spendlogs` |
