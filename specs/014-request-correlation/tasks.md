# Tasks: Request Correlation

**Input**: Design documents from `specs/014-request-correlation/`

**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/response-headers.md ✓

**Tests**: Smoke-test probes only (curl-based, as required by constitution §V falsifiable criteria). No unit tests — this is a pure configuration feature with no new application code.

**Note**: This is a configuration-only feature. All changes are YAML files, one Lua snippet in a Kong plugin, and Bash probe additions. No new services are coded from scratch.

---

## Phase 1: Setup

**Purpose**: Create directories and verify existing plugin state before modification.

- [x] T001 Create `services/otel/` directory (does not exist — OTel Collector config lives here)
- [x] T002 [P] Confirm `services/kong/kong.yml` correlation-id plugin is present with `echo_downstream: true` and `generator: uuid` — no change needed if correct
- [x] T003 [P] Check `.env.example` for `OTEL_EXPORTER_OTLP_ENDPOINT` entry — add if missing

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Deploy the OTel Collector and reroute LiteLLM spans through it. Every user story depends on the Collector being present to attach `gateway.request_id` to spans and forward them to Phoenix.

**⚠️ CRITICAL**: US1 (Phoenix trace lookup) and US3 (span correlation) cannot be verified until this phase is complete.

- [x] T004 Create `services/otel/otel-collector.yml` with OTLP receiver (HTTP :4318, gRPC :4317), `attributes` processor inserting `gateway.request_id` from `http.request.header.x_request_id`, `batch` processor, and `otlp/phoenix` exporter pointing to `http://arize-phoenix:6006`
- [x] T005 Add `otel-collector` service to `docker-compose.yml` under `obs` profile: image `otel/opentelemetry-collector-contrib:0.97.0`, mounts `services/otel/otel-collector.yml`, exposes 4317 and 4318, depends on `arize-phoenix`, healthcheck on `/` port 13133
- [x] T006 Update `OTEL_EXPORTER_OTLP_ENDPOINT` in `docker-compose.yml` LiteLLM environment block from `http://arize-phoenix:6006/v1/traces` to `http://otel-collector:4318/v1/traces` so LiteLLM spans also receive `gateway.request_id` enrichment via the Collector
- [x] T007 Update `OTEL_EXPORTER_OTLP_ENDPOINT` value in `.env.example` to reflect the new Collector endpoint

**Checkpoint**: Run `make up-obs`. Confirm `otel-collector` container is healthy and LiteLLM logs show OTLP export to `otel-collector:4318`.

---

## Phase 3: User Story 1 — Debug a Failed Inference Request End-to-End (Priority: P1) 🎯 MVP

**Goal**: Every response carries a UUID in `X-Request-ID`. That UUID appears in the gateway access log and in a Phoenix Arize trace (via `gateway.request_id` span attribute). An operator can go from response header to full trace in one lookup.

**Independent Test**: Make one request via `curl -si http://localhost:8080/health`, capture the `X-Request-ID` header value, query Phoenix at `http://localhost:6006` and filter spans by `gateway.request_id = <UUID>` — the Kong gateway span appears within 30 seconds.

### Implementation for User Story 1

- [x] T008 [US1] Add `opentelemetry` global plugin to `services/kong/kong.yml` global plugins section: `endpoint: http://otel-collector:4318/v1/traces`, `resource_attributes.service.name: kong-gateway`, `propagation.default_format: w3c`, `batch_span_count: 200`, `batch_flush_delay: 3` — this generates `traceparent`/`tracestate`, captures `X-Request-ID` as `http.request.header.x_request_id` span attribute, and emits spans to the Collector
- [x] T009 [US1] Add smoke probe T001 to `scripts/smoke-test.sh` under a `# ── Request correlation probes (feature 014)` section: `curl -si http://localhost:8080/health` captures `X-Request-ID` header; fail if absent
- [x] T010 [US1] Add smoke probe T002 to `scripts/smoke-test.sh`: validate `X-Request-ID` value matches UUID v4 regex `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`
- [x] T011 [US1] Add smoke probe T004 to `scripts/smoke-test.sh`: make two sequential requests to `/health`, assert both `X-Request-ID` values are non-empty and not equal to each other (uniqueness check)

**Checkpoint**: `make smoke` passes T001, T002, T004. `X-Request-ID` UUID is visible in Phoenix span attribute `gateway.request_id` within 30 s of the request.

---

## Phase 4: User Story 2 — Prevent Clients from Injecting Their Own Trace IDs (Priority: P2)

**Goal**: Any `X-Request-ID`, `traceparent`, or `tracestate` header sent by a client is silently discarded. The gateway always substitutes its own values. No client value reaches a downstream service or appears in the response.

**Independent Test**: `curl -si -H "X-Request-ID: 00000000-0000-0000-0000-000000000000" http://localhost:8080/health` — the returned `X-Request-ID` must differ from the supplied value.

### Implementation for User Story 2

- [x] T012 [US2] Add `pre-function` global plugin to `services/kong/kong.yml` global plugins section (insert BEFORE the existing `correlation-id` entry for readability — Kong execution order is governed by priority, not YAML order): `config.access` Lua block calls `kong.request.clear_header("X-Request-ID")`, `kong.request.clear_header("traceparent")`, `kong.request.clear_header("tracestate")`
- [x] T013 [US2] Add smoke probe T003 to `scripts/smoke-test.sh`: supply `X-Request-ID: 00000000-0000-0000-0000-000000000000` in request; assert returned `X-Request-ID` differs from the supplied value and is a valid UUID
- [x] T014 [US2] Add smoke probe T005 to `scripts/smoke-test.sh`: make an unauthenticated POST to `/v1/chat/completions` (returns 401); assert the 401 response includes a non-empty `X-Request-ID` header (FR-003 — UUID emission decoupled from upstream outcome)

**Checkpoint**: `make smoke` passes T003 and T005. Confirmed with crafted client headers: zero client values in downstream logs.

---

## Phase 5: User Story 3 — Correlate Guardrails and LiteLLM Spans to a Single Request (Priority: P2)

**Goal**: The `traceparent` header forwarded by Kong is received by guardrails and LiteLLM. LiteLLM's Phoenix span is a child of the Kong gateway span. `X-Request-ID` appears in the guardrails audit log entry for the same request.

**Independent Test**: Make one authenticated inference request. Confirm (1) guardrails access log contains the `request_id` field matching the response `X-Request-ID`, and (2) Phoenix shows the LiteLLM span as a child of the Kong span using the same W3C trace ID.

### Implementation for User Story 3

- [x] T015 [US3] Verify guardrails service (`services/guardrails/main.py`) reads the forwarded `X-Request-ID` header and writes it to the audit log as the `request_id` field — if the field is absent from the audit log schema, add header extraction and log field assignment
- [x] T016 [US3] Add smoke probe to `scripts/smoke-test.sh` (authenticated, requires `SMOKE_API_KEY`): make one inference request, capture `X-Request-ID` from response, query `make logs svc=guardrails` and grep for the UUID — fail if not found within the most recent log lines
- [x] T017 [US3] Add Phoenix span-linkage note to `scripts/smoke-test.sh` as `[INFO]` line: "POST /v1/chat/completions — verify LiteLLM child span in Phoenix at http://localhost:6006 using gateway.request_id = <UUID>" (manual verification step — cannot be automated in curl-based smoke test)

**Checkpoint**: `make logs svc=guardrails | grep <UUID>` returns the audit entry. Phoenix UI shows Kong span and LiteLLM span under same trace ID.

---

## Phase 6: User Story 4 — Identify All Requests from a Specific Consumer in a Time Window (Priority: P3)

**Goal**: Each request from a consumer gets its own distinct UUID. Filtering gateway access logs by consumer identity returns a list of unique request IDs, each independently resolvable to a full trace.

**Independent Test**: Send five sequential requests with the same API key. Capture all five `X-Request-ID` values. Confirm all five are distinct UUIDs and each appears in the gateway access log.

### Implementation for User Story 4

- [x] T018 [US4] Confirm Kong access log format includes both `consumer.username` and `x_request_id` fields — if not, update the Kong log serializer or `request-transformer` response header to expose consumer identity alongside the UUID (check `kong.yml` or Makefile log configuration)
- [x] T019 [US4] Add smoke probe to `scripts/smoke-test.sh` (requires `SMOKE_API_KEY`): send 5 sequential requests to `/health`, collect all 5 `X-Request-ID` values into an array, assert all 5 are non-empty and all 5 are distinct (no duplicates) — fail if any duplicate or empty value found

**Checkpoint**: `make smoke` passes the 5-request uniqueness probe. `make logs svc=kong` shows each of the 5 UUIDs on its own line with the consumer name.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Validation, docs update, and progress tracking update.

- [x] T020 [P] Update `docs/progress.md` to mark feature 014 as active and record completed phases
- [ ] T021 [P] Run `make smoke` in full — all existing probes (features 001–013) must still pass (no regressions from Kong config changes)
- [ ] T022 Run `make stats` with `obs` profile — confirm total memory stays within the ~2.15 GB obs-profile budget
- [x] T023 [P] Verify `services/kong/kong.yml` passes declarative config validation: `docker run --rm -v $(pwd)/services/kong:/kong/declarative kong:3.6 kong config parse /kong/declarative/kong.yml`
- [ ] T024 Restart Kong with updated config (`make restart svc=kong`) and confirm `make ps` shows kong healthy before running smoke tests
- [x] T025 [P] Update `.env.example` with any new environment variable names introduced in this feature (verify `OTEL_EXPORTER_OTLP_ENDPOINT` is present)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 complete — BLOCKS Phoenix trace verification in all stories
- **US1 (Phase 3)**: Depends on Phase 2 complete (OTel Collector must be running)
- **US2 (Phase 4)**: Depends on Phase 1 only — `pre-function` plugin is independent of the Collector; can be worked in parallel with Phase 2
- **US3 (Phase 5)**: Depends on Phase 2 (Collector) + Phase 3 (opentelemetry plugin emitting spans)
- **US4 (Phase 6)**: Depends on Phase 3 (UUID generation confirmed working)
- **Polish (Phase 7)**: Depends on all prior phases

### User Story Dependencies

- **US1 (P1)**: Depends on Foundational (Phase 2) — OTel Collector required for Phoenix span lookup
- **US2 (P2)**: Independent of US1 — pre-function plugin only touches Kong config; can be done in parallel with Phase 2
- **US3 (P2)**: Depends on US1 complete (opentelemetry plugin must be active and emitting spans)
- **US4 (P3)**: Depends on US1 complete (UUID generation confirmed)

### Parallel Opportunities

- T002 and T003 (Phase 1) — different files, run together
- T004, T005, T006, T007 (Phase 2) — T004 must complete before T005 (docker-compose references the file); T006 and T007 are independent
- T009, T010, T011 (Phase 3 smoke probes) — all read-only additions to smoke-test.sh, run in parallel
- T012 (Phase 4) and Phase 2 work — different files, can be done in parallel
- T020 and T021 and T025 (Phase 7) — independent files

---

## Parallel Example: Phase 2 + Phase 4 (US2) simultaneously

```text
Worker A: T004 → T005 → T006 → T007  (OTel Collector pipeline)
Worker B: T012 → T013 → T014          (pre-function plugin + US2 probes)
```

Both can land their changes and be committed independently. Phase 3 begins after both workers are done.

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 (Setup — ~5 min)
2. Complete Phase 2 (OTel Collector — T004–T007)
3. Complete Phase 3 (US1 — T008–T011, opentelemetry plugin + smoke probes)
4. **STOP and VALIDATE**: `make smoke` passes T001–T004; Phoenix shows `gateway.request_id` on spans
5. Ship US1 — operators can now trace any request end-to-end

### Incremental Delivery

1. Phase 1 + Phase 2 → OTel pipeline live
2. Phase 3 (US1) → X-Request-ID in response + Phoenix trace linkage ✓
3. Phase 4 (US2) → client header injection blocked ✓
4. Phase 5 (US3) → guardrails + LiteLLM correlated ✓
5. Phase 6 (US4) → per-consumer UUID audit ✓
6. Phase 7 → smoke clean, no regressions, memory budget confirmed ✓

### Acceptance Criteria Mapping

| Smoke Probe | Success Criterion |
|---|---|
| T001 (T009 in tasks) | SC-001 — X-Request-ID present on all responses |
| T002 (T010 in tasks) | SC-001 — UUID format confirmed |
| T003 (T013 in tasks) | SC-004 — client value overwritten |
| T004 (T011 in tasks) | SC-001 — unique across requests |
| T005 (T014 in tasks) | SC-001 — present on error responses |
| US3 guardrails probe (T016) | SC-002 — UUID in access log matches response |
| US4 uniqueness probe (T019) | SC-001 / FR-007 — no duplicates |
| Phoenix manual check (T017) | SC-003 — span discoverable within 30s |
| Latency baseline (T021) | SC-006 — no measurable latency regression |

---

## Notes

- [P] tasks touch different files — safe to run in parallel
- [Story] label traces each task back to the user story it delivers
- No application code is written — every task is a YAML edit, Lua snippet, or Bash probe
- Commit after each phase checkpoint to keep git history clean
- `make restart svc=kong` is required after any `kong.yml` change; `make restart svc=otel-collector` after `otel-collector.yml` changes
- The `pre-function` plugin Lua runs in the Kong `access` phase at priority 1,000,000 — no additional ordering config needed
