# Tasks: API Key Budget Enforcement & Spend Tracking

**Input**: Design documents from `specs/009-key-budget-spend/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/ ✅ | quickstart.md ✅

**Note**: No TDD approach requested — contract tests are included only for the portal-backend service (T-03 in plan.md), which contains aggregation logic worth verifying with mocked responses.

**User Stories**:
- **US1**: Per-Request Cost Attribution (P1)
- **US2**: Hard Budget Enforcement (P1)
- **US3**: Spend Report by Key and Model (P2)
- **US4**: Langfuse Prompt-Version Cost Linkage (P2)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the portal-backend service skeleton. All other changes are to existing files.

- [x] T001 Create `services/portal-backend/` directory with empty `__init__.py`
- [x] T002 [P] Create `services/portal-backend/requirements.txt` with pinned deps: `fastapi==0.115.0`, `uvicorn[standard]==0.32.0`, `httpx==0.27.2`
- [x] T003 [P] Create multi-stage `services/portal-backend/Dockerfile` (builder + runtime stage, non-root user `appuser`, `linux/amd64`+`linux/arm64` targets, pinned `python:3.12-slim` base)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Configuration changes that activate LiteLLM's native spend tracking and key management, and route admin endpoints through Kong. US1 and US2 are entirely native to LiteLLM — they become active once these changes land.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [x] T004 Add `LITELLM_SALT_KEY: ${LITELLM_SALT_KEY}` to the `litellm` service `environment` block in `docker-compose.yml` (immediately after the `LITELLM_MASTER_KEY` line)
- [x] T005 Add `litellm-admin` service definition to `services/kong/kong.yml` pointing to `http://litellm:4000` with four routes: `POST /v1/key/generate` → `/key/generate`, `GET /v1/key/info` → `/key/info`, `POST /v1/key/update` → `/key/update`, `POST /v1/key/delete` → `/key/delete` — all with `strip_path: true`, `paths: [/v1/key]`, no `key-auth` plugin (LiteLLM enforces master key on all `/key/*` endpoints)
- [x] T006 [P] Add `LITELLM_SALT_KEY=` (empty value, no secrets) to `.env.example` under the `# ── LiteLLM ──` section comment, after `LITELLM_SALT_KEY=` which already exists — confirm it is present; if missing, add it

**Checkpoint**: `make restart svc=litellm` + `make restart svc=kong` → `POST http://localhost:8080/v1/key/generate` with master key returns HTTP 200.

---

## Phase 3: User Stories 1 & 2 — Cost Attribution & Budget Enforcement (Priority: P1) 🎯 MVP

**Goal**: Every inference request records its USD cost against the issuing virtual key (US1). Keys configured with `max_budget` reject further requests when spend is exhausted (US2). Both behaviours are LiteLLM-native and activate together once Phase 2 is complete.

**Independent Test**:
```bash
# US1: spend attributed to key
TEAM_KEY=$(curl -s -X POST http://localhost:8080/v1/key/generate \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"key_alias":"us1-test","max_budget":null,"budget_duration":"monthly"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['key'])")
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${TEAM_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'
# Verify spend > 0 in LiteLLM DB: docker exec <postgres> psql -U $POSTGRES_USER litellm \
#   -c "SELECT spend FROM \"LiteLLM_VerificationToken\" WHERE key_alias='us1-test';"

# US2: budget enforcement
TINY_KEY=$(curl -s -X POST http://localhost:8080/v1/key/generate \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"key_alias":"us2-test","max_budget":0.000001,"budget_duration":"monthly"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['key'])")
# After one request exhausts budget:
HTTP=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${TINY_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"second request"}]}')
echo "Expected 429, got: ${HTTP}"
```

### Implementation for User Stories 1 & 2

- [x] T007 [US1] [US2] Create `scripts/provision-key.sh` — bash script wrapping `POST /v1/key/generate` with CLI flags `--alias <name>`, `--budget <usd>`, `--models <csv>` and printing the returned key value with a save-it-now reminder; `chmod +x` the file
- [x] T008 [P] [US1] [US2] Add budget enforcement smoke probes to `scripts/smoke-test.sh`: (a) `POST /v1/key/generate` unauthenticated → 401, (b) `POST /v1/key/generate` with master key → 200 + `key` field present in response body
- [x] T009 [US1] [US2] Verify spend accumulation end-to-end: run the independent test above in `make smoke` context and confirm `[PASS]` lines — document any required `.env` additions in a comment at the top of `provision-key.sh`

**Checkpoint**: `make smoke` passes T008 probes. `provision-key.sh --alias mvp-test --budget 0.01 --models gpt-4o-mini` creates a key; a chat completion with that key succeeds; a second completion after budget exhaustion returns HTTP 429.

---

## Phase 4: User Story 3 — Spend Report by Key and Model (Priority: P2)

**Goal**: `GET /v1/spend` (master key only) returns `{ total_spend_usd, by_model[], by_key[] }` for the current calendar month.

**Independent Test**:
```bash
curl -s http://localhost:8080/v1/spend \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert 'total_spend_usd' in d, 'missing total_spend_usd'
assert isinstance(d['by_model'], list), 'by_model must be list'
assert isinstance(d['by_key'], list), 'by_key must be list'
print('PASS')
"
```

### Contract Tests for User Story 3

- [x] T010 [P] [US3] Create `tests/contract/test_spend_endpoint.py` with pytest fixtures that mock LiteLLM's `/global/spend/keys` and `/global/spend/models` responses using `httpx` — write the following test stubs (they will fail until T011 implements the handler): happy path, LiteLLM 401 → portal 401, LiteLLM 503 → portal 503 `spend_store_unavailable`, `key_id` filter returns single key, `total_spend_usd` equals sum of `by_model[].spend_usd`, `budget_remaining_usd` is `null` when `max_budget_usd` is `null`

### Implementation for User Story 3

- [x] T011 [US3] Implement `services/portal-backend/main.py`: FastAPI app with `GET /v1/spend` endpoint — full type annotations, `httpx.AsyncClient` for upstream calls, aggregation logic per `contracts/get-v1-spend.md`, `GET /health` endpoint returning `{"status":"ok"}`, all error paths (401, 503) with structured error body `{"error": "...", "message": "...", "detail": {}}`
- [x] T012 [US3] Add `portal-backend` service to `docker-compose.yml` under `profiles: [core]` with env vars `LITELLM_BASE_URL: http://litellm:4000` and `LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}`, `expose: ["8092"]`, `depends_on: litellm (service_healthy)`, healthcheck on `/health`, `restart: unless-stopped`
- [x] T013 [US3] Add `portal-backend` service to `services/kong/kong.yml`: service URL `http://portal-backend:8092`, route `GET /v1/spend` with `strip_path: false`, no `key-auth` plugin (portal-backend validates master key via LiteLLM proxy call)
- [x] T014 [P] [US3] Add `LITELLM_MASTER_KEY` reference to portal-backend section in `.env.example` under a new comment `# ── Portal backend ──` if not already present — value must remain empty
- [x] T015 [US3] Add spend report smoke probes to `scripts/smoke-test.sh`: (a) `GET /v1/spend` unauthenticated → 401, (b) `GET /v1/spend` with master key → 200 + assert `total_spend_usd`, `by_model`, `by_key` fields present
- [x] T016 [US3] Run `tests/contract/test_spend_endpoint.py` and confirm all 6 test cases pass: `python -m pytest tests/contract/test_spend_endpoint.py -v`

**Checkpoint**: `make up-core` → `GET http://localhost:8080/v1/spend` with master key returns HTTP 200 with correct shape. `make smoke` passes all spend probes. All 6 contract tests pass.

---

## Phase 5: User Story 4 — Langfuse Prompt-Version Cost Linkage (Priority: P2)

**Goal**: Any inference request carrying `metadata.langfuse_prompt_name` and `metadata.langfuse_prompt_version` produces a Langfuse trace with `cost`, `usage.prompt_tokens`, `usage.completion_tokens`, and the prompt attribution fields — queryable by prompt version in Langfuse dashboard.

**Independent Test** (requires `make up-obs` with valid Langfuse credentials):
```bash
TEAM_KEY="<key from Phase 3>"
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ${TEAM_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Summarise this doc."}],
    "metadata": {
      "langfuse_prompt_name": "document-summariser",
      "langfuse_prompt_version": "3"
    }
  }'
# In Langfuse UI (http://localhost:3002): filter traces by prompt name,
# confirm cost > 0 and prompt_version = "3" appear as trace metadata.
```

### Implementation for User Story 4

- [x] T017 [US4] Verify `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` are all set in `.env` and forwarded to the litellm service in `docker-compose.yml` — they are already present; confirm by grepping `docker-compose.yml` and add a comment block in `quickstart.md` step 4 noting that no code changes are required for this user story (the `langfuse` callback handles cost metadata automatically)
- [x] T018 [P] [US4] Add a Langfuse metadata smoke probe to `scripts/smoke-test.sh`: issue a chat completion with `metadata.langfuse_prompt_name=smoke-test` and `metadata.langfuse_prompt_version=1`; verify the request returns HTTP 200 (the trace appearance in Langfuse is validated manually per quickstart.md step 4, not by the smoke script)
- [x] T019 [US4] Add a `# Langfuse prompt-version cost linkage` section to `quickstart.md` if not already present — cross-reference step 4 and explain how to filter by `langfuse_prompt_name` in the Langfuse dashboard to sum `cost` per version

**Checkpoint**: Chat completion with `metadata.langfuse_prompt_name` returns HTTP 200. Langfuse trace (visible at `http://localhost:3002`) shows `cost` field > 0 and prompt attribution metadata.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end validation, memory budget check, and documentation completeness.

- [ ] T020 [P] Run `make stats` after `make up-core` and confirm portal-backend memory footprint keeps total under the `core`-only budget (~620 MB); document actual figure in a comment at the top of `services/portal-backend/Dockerfile`
- [ ] T021 Run `make smoke` with `LITELLM_MASTER_KEY` and `SMOKE_API_KEY` set — all probes must show `[PASS]`; fix any failures before marking this task done
- [x] T022 [P] Run `ruff check services/portal-backend/` and `mypy services/portal-backend/ --ignore-missing-imports` — zero errors required; fix any findings

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 completion — **blocks all user stories**
- **US1 & US2 (Phase 3)**: Depends on Phase 2 — LiteLLM native, no portal-backend needed
- **US3 (Phase 4)**: Depends on Phase 2 + Phase 1 (portal-backend files must exist)
- **US4 (Phase 5)**: Depends on Phase 2 only — no new code; can run in parallel with Phase 4
- **Polish (Phase 6)**: Depends on Phases 3, 4, and 5 all complete

### User Story Dependencies

- **US1 & US2 (P1)**: Start after Phase 2 — independent of US3 and US4
- **US3 (P2)**: Start after Phase 2 — independent of US1/US2 except they share the LiteLLM backend
- **US4 (P2)**: Start after Phase 2 — fully independent; requires `make up-obs` for visual verification only

### Within Each Phase

- Models before services, services before endpoints
- Contract tests written (T010) before implementation (T011)
- Docker service added (T012) before Kong route (T013)

### Parallel Opportunities

- T002 and T003 (Phase 1) — different files, run together
- T005 and T006 (Phase 2) — different files, run together
- T008 (Phase 3) and T010 (Phase 4) — different files, run together once Phase 2 is done
- T014 (Phase 4) and T018 (Phase 5) — different files, run together
- T020 and T022 (Phase 6) — different targets, run together

---

## Parallel Example: Phase 4 (US3)

```bash
# Run contract test stub creation and .env.example update in parallel:
Task T010: "Create tests/contract/test_spend_endpoint.py with 6 mocked test cases"
Task T014: "Add portal-backend env vars to .env.example"

# After T010 and T011 complete, run Kong config and docker-compose in parallel:
Task T012: "Add portal-backend service to docker-compose.yml"
Task T013: "Add portal-backend Kong route to services/kong/kong.yml"
```

---

## Implementation Strategy

### MVP First (US1 + US2 Only — Phase 1 → 3)

1. Complete Phase 1: Setup (T001–T003)
2. Complete Phase 2: Foundational (T004–T006) — restart LiteLLM + Kong
3. Complete Phase 3: US1 & US2 (T007–T009)
4. **STOP and VALIDATE**: `provision-key.sh`, issue inference request, verify spend tracked, exhaust budget, confirm 429
5. Budget enforcement is live with zero portal-backend code

### Incremental Delivery

1. Phases 1–3 → Budget enforcement live (MVP)
2. Phase 4 → Spend report live; operators can query `GET /v1/spend`
3. Phase 5 → Prompt-version cost visible in Langfuse dashboard
4. Phase 6 → Polish; `make smoke` green

### Solo Developer Sequencing

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6
```

Total: ~22 tasks. Phase 3 (US1/US2) has zero new application code — purely configuration + one script. Phase 4 (US3) contains the only substantive code (portal-backend FastAPI service).

---

## Notes

- `[P]` tasks touch different files and have no incomplete dependencies — safe to run in parallel
- `[US1]` / `[US2]` labels on Phase 3 tasks indicate both stories are served by the same tasks
- US1 and US2 require no application code — LiteLLM's native spend tracking activates automatically when `LITELLM_SALT_KEY` and `DATABASE_URL` are set and a key is created via `POST /key/generate`
- US4 requires no application code — the `langfuse` callback already sends cost metadata; this phase is validation + documentation only
- All constitution checks remain green throughout — no prompt content ever persists; Kong → Guardrails → LiteLLM inference chain is not modified
