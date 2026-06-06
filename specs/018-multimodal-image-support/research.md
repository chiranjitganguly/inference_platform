# Research: Multimodal Image Support

**Branch**: `018-multimodal-image-support` | **Date**: 2026-06-06

## Findings

---

### Decision 1: Vision capability declaration

**Decision**: Use the existing `capabilities: [vision]` list in `model_info` within `services/litellm/config.yaml`. The spec phrase `supports_vision: true` maps to `"vision" in model_info.capabilities`.

**Rationale**: The convention already exists in config (four of nine models already carry it). Introducing a parallel `supports_vision: true` key would create two sources of truth for the same fact.

**Alternatives considered**: Adding a separate `supports_vision: true` scalar field to `model_info`. Rejected: redundant with the existing `capabilities` list.

---

### Decision 2: Current vision capability state in config

**Decision**: Treat the `capabilities` list as the authoritative gate. Current state:

| Model | Has `vision` in capabilities? | Action |
|---|---|---|
| `gpt-4o` | Yes | No change |
| `gpt-4.1` | Yes | No change |
| `claude-sonnet` | Yes | No change |
| `gemini-pro` | Yes | No change |
| `gemini-flash` | **No** | Add `vision` (user confirmed gemini-flash supports vision) |
| `gpt-4o-mini` | **No** | Add `vision` (spec assumption; OpenAI gpt-4o-mini supports images) |
| `claude-haiku` | No | Leave as-is (Haiku 4.5 does not support vision natively in this tier) |
| `o4-mini` | No | Leave as-is |
| `command-r-plus` | No | Leave as-is |
| `text-embedding-*` | No | Leave as-is |

---

### Decision 3: Vision-capable model list in Guardrails service

**Decision**: Build the vision capability set at Guardrails startup by querying LiteLLM's `/model/info` endpoint and caching the result in memory. Re-read on SIGHUP for zero-downtime config updates.

**Rationale**: Single source of truth stays in `config.yaml`. No environment variable duplication. LiteLLM's `/model/info` returns `model_info.capabilities` for each registered model.

**Alternatives considered**:
- `VISION_CAPABLE_MODELS` env var (comma-separated). Rejected: creates a second place to maintain the list.
- Hardcoded set in Guardrails. Rejected: breaks whenever config.yaml changes.

---

### Decision 4: Fallback chain alignment for vision requests

**Decision**: Update the fallback chains in `config.yaml` so that any model with `vision` capability only lists other vision-capable models as fallbacks.

Affected chains:
- `gemini-flash → [gpt-4o-mini, claude-haiku]` → change to `[gpt-4o-mini, gemini-pro]` (after `gpt-4o-mini` gains vision)
- `gpt-4o-mini → [claude-haiku, gemini-flash]` → change to `[gemini-flash, claude-sonnet]`

All other vision-model primary fallback chains already point exclusively to vision models.

**Rationale**: FR-009 requires non-vision models to not appear in vision fallback paths. If a non-vision model is tried by LiteLLM and the upstream provider rejects the image content, it counts as a wasted API call and increased latency.

**Alternatives considered**: Guardrails intercepting the fallback at the HTTP layer. Rejected: LiteLLM handles fallbacks internally; guardrails cannot intercept mid-LiteLLM retry.

---

### Decision 5: `detail` field default

**Decision**: Default `detail` to `"auto"` when a caller omits it.

**Rationale**: `auto` delegates the cost/quality tradeoff to the upstream provider's own heuristic, which is the safest default in a multi-provider gateway. The Q1 clarification question was not answered before `/speckit-plan` was invoked; `auto` was the recommended option.

**Alternatives considered**: `low` (cheapest, may miss detail), `high` (best quality, highest cost), reject-if-omitted (too strict for an optional field).

---

### Decision 6: Image validation scope

**Decision**: Guardrails validates:
1. Content array contains at least one `text` part alongside image parts (images must not be the sole content)
2. Each image part `url` field is either a valid `https://` URL or a well-formed `data:image/<mime>;base64,<data>` URI
3. `detail` value, if provided, is one of `"low"`, `"high"`, `"auto"`
4. Image count per request does not exceed the configured maximum (default: 5)
5. Requested model name is in the vision-capable set

Guardrails does **not** fetch URL images (provider does that). Content-level image analysis (NSFW, etc.) is out of scope.

---

### Decision 7: Phoenix image token count

**Decision**: LiteLLM's `arize_phoenix` callback forwards the full `usage` object from the provider response, which includes `prompt_tokens`. For vision models, providers typically embed image token cost in `prompt_tokens` rather than a separate field. Guardrails does not modify Phoenix spans — the token count arrives via LiteLLM's existing callback mechanism.

When providers return a separate image-specific token field (e.g., OpenAI's `prompt_tokens_details.audio_tokens` analogue), it appears in the span if LiteLLM propagates it. No custom span attributes need to be written by Guardrails.

**Rationale**: Keeping observability in the LiteLLM callback layer respects the architecture's single-responsibility boundary. Guardrails is a policy layer, not an observability layer.

---

### Decision 8: Streaming exclusion

**Decision**: If a vision request arrives with `"stream": true`, Guardrails returns HTTP 400 with `error: "vision_streaming_not_supported"`.

**Rationale**: The spec explicitly excludes streaming for the initial release. Fail fast rather than passing the request through and getting an inconsistent result.
