---
description: "Task list for feature 011 — Embeddings Endpoint"
---

# Tasks: Embeddings Endpoint (011)

**Input**: Design documents from `specs/011-embeddings-endpoint/`

**Branch**: `011-embeddings-endpoint`

**Organization**: Tasks are grouped by user story to enable independent implementation and
testing of each story.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies on each other)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)

---

## Phase 1: Foundational Configuration

**Purpose**: The two config changes that unblock all three user stories. Neither touches application
code — both are pure config edits in different files and can be applied in parallel.

**⚠️ CRITICAL**: No user story smoke test can pass until this phase is complete.

- [x] T001 [P] Remove `aembedding` and `embedding` from `cache_params.supported_call_types` in `services/litellm/config.yaml` (FR-006 — embedding cache bypass; leave `acompletion` and `completion` intact)
- [x] T002 [P] Add `litellm-embeddings` service entry (`url: http://litellm:4000`, `read_timeout: 120000`, `write_timeout: 120000`) with a `/v1/embeddings` route (`methods: [POST]`, `strip_path: false`) and a `key-auth` plugin (`key_names: [Authorization]`, `key_in_header: true`, `hide_credentials: true`) in `services/kong/kong.yml`

**Checkpoint**: Restart LiteLLM and Kong (`make restart svc=litellm && make restart svc=kong`). The `/v1/embeddings` path is now reachable and unauthenticated requests return HTTP 401.

---

## Phase 2: User Story 1 — Convert Text to Embeddings (Priority: P1) 🎯 MVP

**Goal**: Callers POST text to `/v1/embeddings` with `text-embedding-3-small` or
`text-embedding-3-large` and receive the correct float array dimensions plus token usage. Phoenix
and Langfuse do not receive these calls.

**Independent Test**:
```bash
curl -s -X POST http://localhost:8080/v1/embeddings \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"text-embedding-3-small","input":"Hello world"}' \
  | jq '{dims:(.data[0].embedding|length), tokens:.usage.total_tokens}'
# Expected: {"dims":1536,"tokens":2}
```

### Implementation for User Story 1

- [x] T003 [US1] Add `_inject_no_log()` helper and embedding path detection in `services/guardrails/main.py`: (a) add `_inject_no_log(body: bytes) -> bytes` that parses JSON body, sets `payload.setdefault("metadata", {})["no_log"] = "True"`, and returns re-serialised bytes; (b) in `proxy()`, before the `httpx` call, add `if path == "v1/embeddings" and request.method == "POST": body = _inject_no_log(body)` — this suppresses Phoenix/Langfuse callbacks for embedding requests (FR-008) while leaving Prometheus unaffected
- [x] T004 [US1] Add three embedding smoke test probes to `scripts/smoke-test.sh` inside the `if [[ -n "$SMOKE_API_KEY" ]]` block: (1) `text-embedding-3-small` single input → assert `jq '.data[0].embedding|length == 1536'`; (2) `text-embedding-3-large` single input → assert `jq '.data[0].embedding|length == 3072'`; (3) batch of 3 inputs with `text-embedding-3-small` → assert `jq '.data|length == 3 and .data[0].index == 0'`

**Checkpoint**: User Story 1 is independently verifiable. `make smoke` passes the three new probes. Both embedding models return arrays of the correct dimensionality.

---

## Phase 3: User Story 2 — Reject Chat Models on Embeddings Endpoint (Priority: P1)

**Goal**: Any non-embedding model name submitted to `/v1/embeddings` is rejected with HTTP 400
before the upstream provider is called. Enforced natively by LiteLLM's model type validation —
no application code change required beyond what Phase 1 already enables.

**Independent Test**:
```bash
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8080/v1/embeddings \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","input":"Hello"}'
# Expected: 400
```

### Implementation for User Story 2

- [x] T005 [US2] Add chat-model-rejection smoke test probe to `scripts/smoke-test.sh`: POST `/v1/embeddings` with `model: gpt-4o` → assert HTTP status is `400`; add a second probe with `model: claude-sonnet` → assert HTTP status is `400`

**Checkpoint**: User Story 2 is independently verifiable. `make smoke` confirms both chat model probes return HTTP 400 without making upstream calls.

---

## Phase 4: User Story 3 — Token Usage Reporting (Priority: P2)

**Goal**: Every successful embedding response contains non-zero `usage.prompt_tokens` and
`usage.total_tokens`. No code change required — LiteLLM emits usage fields for all embedding
calls. This phase adds the smoke test assertion to confirm the behaviour.

**Independent Test**:
```bash
curl -s -X POST http://localhost:8080/v1/embeddings \
  -H "Authorization: Bearer ${SMOKE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"text-embedding-3-small","input":"token usage check"}' \
  | jq '.usage.total_tokens > 0'
# Expected: true
```

### Implementation for User Story 3

- [x] T006 [US3] Add token usage smoke test probe to `scripts/smoke-test.sh`: POST `/v1/embeddings` with `text-embedding-3-small` → assert `jq '.usage.prompt_tokens > 0 and .usage.total_tokens > 0'` returns `true`

**Checkpoint**: User Story 3 is independently verifiable. All three user stories are now functional and smoke-tested.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Cache bypass verification, code quality gates, and full smoke run.

- [x] T007 Add cache-bypass smoke test probe to `scripts/smoke-test.sh` inside the `if [[ -n "$SMOKE_API_KEY" ]]` block: send two identical embedding requests (`text-embedding-3-small`, same input), capture the `x-litellm-cache-hit` response header on each — assert neither response contains `x-litellm-cache-hit: True` (SC-006; absence of the header or a `False` value both indicate a live upstream call)
- [x] T008 Run quality gates and full smoke test: `ruff check services/guardrails/` → zero warnings; `mypy services/guardrails/ --ignore-missing-imports` → zero errors; `make smoke` → all probes pass including the 7 new embedding probes

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 1)**: No dependencies — start immediately. T001 and T002 are in different files and run in parallel.
- **US1 (Phase 2)**: Depends on Phase 1 completion (Kong route and LiteLLM cache config must be applied first)
- **US2 (Phase 3)**: Depends on Phase 1 completion. Can be worked in parallel with Phase 2 since T005 only adds smoke test lines to a different section of `smoke-test.sh` than T004.
- **US3 (Phase 4)**: Depends on Phase 1 completion. Independent of US1/US2 smoke test sections.
- **Polish (Phase 5)**: Depends on Phases 2–4 completion (T007–T008 verify the full set).

### User Story Dependencies

- **US1 (P1)**: Starts after Phase 1 — no dependency on US2 or US3
- **US2 (P1)**: Starts after Phase 1 — no dependency on US1 or US3
- **US3 (P2)**: Starts after Phase 1 — no dependency on US1 or US2

### Within Each Phase

- T001 and T002 can run in parallel (different files)
- T003 must complete before T004 (guardrails change is needed before smoke test captures its behaviour)
- T005 and T006 can be added to `smoke-test.sh` in any order after their respective phases' implementations are done

---

## Parallel Execution Example: Foundational Phase

```
# Both config edits can be applied simultaneously:
Task: "T001 — Remove embedding types from litellm cache supported_call_types in services/litellm/config.yaml"
Task: "T002 — Add Kong litellm-embeddings service and route in services/kong/kong.yml"
```

---

## Implementation Strategy

### MVP (User Stories 1 + 2 — both P1)

1. Complete Phase 1: Foundational Configuration
2. Complete Phase 2: US1 — Convert Text to Embeddings
3. Complete Phase 3: US2 — Reject Chat Models
4. **STOP and VALIDATE**: Run `make smoke` — all embedding probes must pass
5. Ship: Both P1 stories are independently verifiable with `curl`

### Incremental Delivery

1. Phase 1 → Foundation ready (Kong route live, cache bypass active)
2. Phase 2 → US1 complete → smoke test passes for embeddings (MVP!)
3. Phase 3 → US2 complete → rejection verified
4. Phase 4 → US3 complete → token usage verified
5. Phase 5 → Quality gates clean, cache bypass confirmed

---

## Notes

- [P] tasks operate on different files with no shared state — safe to parallelise
- US2 (model type rejection) requires zero application code — LiteLLM enforces it natively via `model_info.type: embedding` in `config.yaml`; the only deliverable is the smoke test assertion
- The Guardrails change in T003 is safe to apply regardless of whether callbacks are enabled — `no_log: True` is a no-op when the global callbacks line in `config.yaml` is commented out
- Smoke test timeout for embedding probes: use `--max-time 15` instead of the default `$TIMEOUT=5` to accommodate real upstream latency
- After T002, verify the Kong route priority: `/v1/embeddings` (more specific) must match before the existing `/v1` catch-all — Kong 3.6 resolves this by path specificity automatically
