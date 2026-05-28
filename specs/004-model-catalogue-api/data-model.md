# Data Model: Model Catalogue API

**Branch**: `004-model-catalogue-api` | **Date**: 2026-05-28

---

## ModelEntry

The atomic unit returned in the catalogue. Each entry corresponds to one model in `services/litellm/config.yaml`.

### Fields

| Field | Type | Required | Allowed Values | Notes |
|---|---|---|---|---|
| `id` | string | yes | any | Platform model name (e.g. `gpt-4o`) |
| `object` | string | yes | `"model"` | OpenAI envelope field |
| `created` | integer | yes | Unix timestamp | Set to LiteLLM default on startup |
| `owned_by` | string | yes | provider name | OpenAI envelope field |
| `model_info.provider` | string | yes | `openai`, `anthropic`, `google`, `cohere` | Owning cloud provider |
| `model_info.tier` | string | yes | `standard`, `premium` | No other values permitted |
| `model_info.type` | string | yes | `chat`, `embedding` | No other values permitted |
| `model_info.status` | string | yes | `available`, `unavailable` | `unavailable` when model is disabled in config |
| `model_info.context_window` | integer | yes | positive integer | Maximum tokens (input + output) |
| `model_info.capabilities` | array of strings | yes | see vocab below | At least one entry required |
| `model_info.dimensions` | integer | if `type == "embedding"` | positive integer | Must be absent when `type == "chat"` |

### Capabilities Vocabulary

Controlled set — only these strings are valid:

| Value | Meaning |
|---|---|
| `chat` | Supports multi-turn conversation |
| `streaming` | Supports streaming token output |
| `function-calling` | Supports tool/function call API |
| `vision` | Accepts image inputs |
| `embeddings` | Returns dense vector representations |

### Invariants

1. `dimensions` MUST be present if and only if `type == "embedding"`.
2. `tier` MUST be exactly `standard` or `premium`.
3. `type` MUST be exactly `chat` or `embedding`.
4. `status` MUST be exactly `available` or `unavailable`.
5. `capabilities` MUST contain `embeddings` if `type == "embedding"`.
6. `capabilities` MUST NOT contain `embeddings` if `type == "chat"`.

---

## Catalogue Envelope

The top-level response shape for `GET /v1/models`.

```
Catalogue
├── object: "list"         (string, always "list")
└── data: ModelEntry[]     (array, minimum 11 entries when all models configured)
```

### Invariants

1. `object` is always `"list"`.
2. `data` contains exactly one entry per model in `services/litellm/config.yaml`.
3. Models from all four providers MUST appear: `openai`, `anthropic`, `google`, `cohere`.
4. Disabled models appear with `status: unavailable` — they are never omitted.

---

## All 11 ModelEntry Instances

### Chat Models (9)

```yaml
- id: gpt-4o
  provider: openai
  tier: premium
  type: chat
  status: available
  context_window: 128000
  capabilities: [chat, streaming, function-calling, vision]

- id: gpt-4o-mini
  provider: openai
  tier: standard
  type: chat
  status: available
  context_window: 128000
  capabilities: [chat, streaming, function-calling]

- id: gpt-4.1
  provider: openai
  tier: premium
  type: chat
  status: available
  context_window: 1047576
  capabilities: [chat, streaming, function-calling, vision]

- id: o4-mini
  provider: openai
  tier: standard
  type: chat
  status: available
  context_window: 128000
  capabilities: [chat, streaming, function-calling]

- id: claude-sonnet
  provider: anthropic
  tier: premium
  type: chat
  status: available
  context_window: 200000
  capabilities: [chat, streaming, function-calling, vision]

- id: claude-haiku
  provider: anthropic
  tier: standard
  type: chat
  status: available
  context_window: 200000
  capabilities: [chat, streaming]

- id: gemini-pro
  provider: google
  tier: premium
  type: chat
  status: available
  context_window: 1048576
  capabilities: [chat, streaming, function-calling, vision]

- id: gemini-flash
  provider: google
  tier: standard
  type: chat
  status: available
  context_window: 1048576
  capabilities: [chat, streaming, function-calling]

- id: command-r-plus
  provider: cohere
  tier: premium
  type: chat
  status: available
  context_window: 128000
  capabilities: [chat, streaming, function-calling]
```

### Embedding Models (2)

```yaml
- id: text-embedding-3-small
  provider: openai
  tier: standard
  type: embedding
  status: available
  context_window: 8191
  capabilities: [embeddings]
  dimensions: 1536

- id: text-embedding-3-large
  provider: openai
  tier: premium
  type: embedding
  status: available
  context_window: 8191
  capabilities: [embeddings]
  dimensions: 3072
```

---

## ApiKey

Opaque bearer token validated by Kong's key-auth plugin before any request reaches LiteLLM.

| Attribute | Notes |
|---|---|
| Format | Arbitrary string passed as `Authorization: Bearer <key>` |
| Storage | Kong consumer credential store (seeded via `make seed-kong`) |
| Validation | Performed entirely by Kong — LiteLLM never sees the raw key |
| Lifecycle | Created / revoked via Kong Admin API |
