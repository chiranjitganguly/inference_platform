# Tasks: Multimodal Image Support

**Input**: Design documents from `specs/018-multimodal-image-support/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/ ✅

**Tests**: Contract tests are included (plan.md specifies `tests/contract/test_multimodal.py`).

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no shared-state dependencies)
- **[Story]**: Maps to user story from spec.md (US1–US4)

---

## Phase 1: Setup — ADR (BLOCKING PREREQUISITE)

**Purpose**: The OpenAI error envelope decision (clarification Q2) conflicts with constitution §4.4. No implementation task may begin until this ADR is committed.

**⚠️ CRITICAL**: ALL phases below are blocked until T001 is merged.

- [x] T001 Write and commit `docs/adr/018-openai-error-envelope.md` — amends constitution §4.4 to permit OpenAI error envelope (`{"error": {"message": "...", "type": "...", "code": "..."}}`) for vision-validation rejections on `/v1/chat/completions`; all other platform errors retain the `{"error": "code", "message": "...", "detail": {}}` schema

**Checkpoint**: ADR merged → implementation phases unblocked

---

## Phase 2: Foundational (Blocking All User Stories)

**Purpose**: LiteLLM vision capability config + Guardrails `VisionCapabilityCache` infrastructure. Must be complete before any user story can be implemented or tested.

**⚠️ CRITICAL**: Depends on T001 (ADR merged). No user story work starts until this phase is complete.

- [x] T002 [P] Add `vision` to `capabilities` list for `gemini-flash` in `services/litellm/config.yaml`
- [x] T003 [P] Add `vision` to `capabilities` list for `gpt-4o-mini` in `services/litellm/config.yaml`
- [x] T004 [P] Update `gemini-flash` fallback chain to `[gpt-4o-mini, gemini-pro]` in `services/litellm/config.yaml` (removes `claude-haiku` — non-vision)
- [x] T005 [P] Update `gpt-4o-mini` fallback chain to `[gemini-flash, claude-sonnet]` in `services/litellm/config.yaml` (removes `claude-haiku` — non-vision)
- [x] T006 Create `services/guardrails/vision.py` with `VisionCapabilityCache` class: async `load()` method calls `GET {LITELLM_BASE_URL}/model/info` with `Authorization: Bearer {LITELLM_MASTER_KEY}`, extracts model names where `"vision" in model_info.capabilities`, stores as `frozenset[str]`; raises `RuntimeError` on unreachable endpoint (fail-fast)
- [x] T007 Register `VisionCapabilityCache` as a FastAPI lifespan singleton in `services/guardrails/main.py`: create on startup, expose via `app.state.vision_cache`; service must not start if cache load fails
- [x] T008 Add `image_part_count: int` field to `_write_audit()` in `services/guardrails/main.py` (0 for text-only requests; counts image parts across all messages; never logs image content)

**Checkpoint**: LiteLLM config updated + `VisionCapabilityCache` loads on startup → user story phases can begin

---

## Phase 3: User Story 1 — Send Image via URL (Priority: P1) 🎯 MVP

**Goal**: A caller sends a chat completion request with an `https://` URL image part. The gateway validates the request, routes to a vision-capable model via LiteLLM, and returns a textual response. Base64 support, multi-image, and rejection tests come in later phases.

**Independent Test**:
```bash
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":[
    {"type":"text","text":"What do you see?"},
    {"type":"image_url","image_url":{"url":"https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png/240px-PNG_transparency_demonstration_1.png"}}
  ]}]}'
# Expected: HTTP 200, choices[0].message.content is non-empty string
```

### Implementation for User Story 1

- [x] T009 [P] [US1] Add `has_image_parts(messages: list[dict]) -> bool` to `services/guardrails/vision.py`: returns `True` if any message has `content` as a list containing an entry with `"type": "image_url"`
- [x] T010 [P] [US1] Add `inject_detail_defaults(messages: list[dict]) -> list[dict]` to `services/guardrails/vision.py`: walks all image parts across all messages, sets `image_url["detail"] = "auto"` where `detail` key is absent; returns modified messages
- [x] T011 [US1] Add `validate_vision_request(body: dict, vision_models: frozenset[str]) -> dict | None` to `services/guardrails/vision.py`: validates in order — (1) stream + image guard → OpenAI envelope 400 `vision_streaming_not_supported`; (2) model in `vision_models` check → OpenAI envelope 400 `vision_model_required` naming the model and listing valid alternatives; (3) https:// URL well-formedness check per image part → OpenAI envelope 400 `invalid_image_url`; returns `None` on pass, error dict on fail
- [x] T012 [US1] Wire vision gate into `proxy()` in `services/guardrails/main.py`: when `path == "v1/chat/completions"` and `request.method == "POST"`, call `has_image_parts` → if true, call `validate_vision_request` → on error return `Response(content=json.dumps(error), status_code=error_status, media_type="application/json")`; on pass call `inject_detail_defaults` and re-serialise body before forwarding; pass `image_part_count` to `_write_audit`
- [x] T013 [US1] Add URL vision smoke test to `scripts/smoke-test.sh` matching AC-1 from `specs/018-multimodal-image-support/plan.md`: POST to `:8080/v1/chat/completions` with URL image + `gpt-4o`, assert HTTP 200 and non-empty `choices[0].message.content`

**Checkpoint**: URL image request returns HTTP 200 with image description → US1 independently functional

---

## Phase 4: User Story 2 — Send Base64-Encoded Image (Priority: P1)

**Goal**: A caller sends a `data:image/...;base64,...` data URI. The gateway validates format and size, then routes identically to the URL path.

**Independent Test**:
```bash
IMAGE_B64=$(curl -s https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png/240px-PNG_transparency_demonstration_1.png | base64 | tr -d '\n')
curl -s -w "\n%{http_code}" -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"gpt-4o\",\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"Describe this.\"},{\"type\":\"image_url\",\"image_url\":{\"url\":\"data:image/png;base64,${IMAGE_B64}\"}}]}]}"
# Expected: HTTP 200, non-empty content
```

### Implementation for User Story 2

- [x] T014 [P] [US2] Add base64 data URI format validation to `validate_vision_request()` in `services/guardrails/vision.py`: check each image `url` matching `^data:image/` against regex `^data:image/(jpeg|png|gif|webp);base64,[A-Za-z0-9+/]+=*$`; reject malformed URIs with OpenAI envelope 400 `invalid_image_data_uri` including `part_index`
- [x] T015 [US2] Add 5 MB per-image size enforcement to `validate_vision_request()` in `services/guardrails/vision.py`: for base64 data URIs, decode the base64 payload and check `len(decoded) > 5 * 1024 * 1024`; reject with OpenAI envelope **413** `image_too_large` including `part_index` and `size_bytes`
- [x] T016 [US2] Add `missing_text_part` validation to `validate_vision_request()` in `services/guardrails/vision.py`: when any image part is detected, verify at least one `{"type": "text"}` part exists across the message content array; reject with OpenAI envelope 400 `missing_text_part`
- [x] T017 [P] [US2] Add `image_count_exceeded` validation to `validate_vision_request()` in `services/guardrails/vision.py`: read `MAX_IMAGES_PER_REQUEST` env var (default `5`), count image parts across all messages, reject with OpenAI envelope 400 `image_count_exceeded` including `count` and `max_allowed`
- [x] T018 [P] [US2] Add `invalid_image_detail` validation to `validate_vision_request()` in `services/guardrails/vision.py`: if `detail` is present and not in `{"low", "high", "auto"}`, reject with OpenAI envelope 400 `invalid_image_detail` including `part_index`, `provided`, `allowed`

**Checkpoint**: Base64 image request returns HTTP 200; malformed URI or oversized image returns structured error → US2 independently functional

---

## Phase 5: User Story 3 — Vision-Incapable Model Rejected (Priority: P2)

**Goal**: Any request with image parts that specifies (or can only fall back to) a non-vision model is rejected at the gateway with an OpenAI-envelope 400 that names the offending model, before any call reaches LiteLLM.

**Independent Test**:
```bash
curl -s -w "\n%{http_code}" -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"command-r-plus","messages":[{"role":"user","content":[{"type":"text","text":"What is this?"},{"type":"image_url","image_url":{"url":"https://example.com/img.jpg"}}]}]}'
# Expected: HTTP 400, body.error.code == "vision_model_required", body.error.message names "command-r-plus"
```

### Implementation for User Story 3

- [x] T019 [US3] Complete OpenAI-envelope 400 `vision_model_required` error body in `validate_vision_request()` in `services/guardrails/vision.py`: `message` must name the requested model; include `vision_capable_models` list in error detail; verify the rejection path is triggered BEFORE any HTTP call to LiteLLM
- [x] T020 [US3] Add no-model-specified + all-fallbacks-non-vision guard in `services/guardrails/vision.py`: when `model` key is absent or empty in the request body AND image parts are present, check if LiteLLM's default routing would produce a vision-capable model; if not determinable, reject with OpenAI envelope 400 `vision_model_required` with `message: "No vision-capable model available for this request"`
- [x] T021 [US3] Add non-vision rejection smoke test to `scripts/smoke-test.sh` matching AC-3 from `specs/018-multimodal-image-support/plan.md`: POST with `command-r-plus` + image part, assert HTTP 400 and `error.code == "vision_model_required"`

**Checkpoint**: Non-vision model + image returns HTTP 400 with structured error and model name; request confirmed absent from LiteLLM access log → US3 independently functional

---

## Phase 6: User Story 4 — Multiple Images in a Single Request (Priority: P3)

**Goal**: A caller sends 2+ image parts (URL, base64, or mixed) in one request. All images are forwarded together; exceeding the limit returns 400.

**Independent Test**:
```bash
# Two URL images in one request — both referenced in response
curl -s -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":[
    {"type":"text","text":"Compare these two images."},
    {"type":"image_url","image_url":{"url":"https://example.com/img1.jpg"}},
    {"type":"image_url","image_url":{"url":"https://example.com/img2.jpg"}}
  ]}]}'
# Expected: HTTP 200, response content references both images
```

### Implementation for User Story 4

- [x] T022 [US4] Verify image count aggregation in `validate_vision_request()` in `services/guardrails/vision.py` counts across ALL messages (not just first message): add a multi-message test path where image parts are spread across two user messages; confirm `image_count_exceeded` fires when combined total > `MAX_IMAGES_PER_REQUEST`
- [x] T023 [US4] Verify `inject_detail_defaults()` in `services/guardrails/vision.py` iterates all image parts across all messages and sets `"auto"` on each part missing `detail`; confirm no part is skipped when multiple user messages each contain image parts

**Checkpoint**: Multi-image request forwards all images; count-exceeded returns 400; detail defaults injected on every part → US4 independently functional

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Contract tests, OpenAI error schema consistency in docs, type/lint checks, full smoke pass.

- [x] T024 [P] Create `tests/contract/test_multimodal.py` with 8 contract test cases from plan.md AC-1 through AC-6: (1) URL image → 200; (2) base64 image → 200; (3) non-vision model → 400 `vision_model_required`; (4) `stream:true` + image → 400 `vision_streaming_not_supported`; (5) malformed base64 → 400 `invalid_image_data_uri`; (6) image count exceeded → 400 `image_count_exceeded`; (7) missing text part → 400 `missing_text_part`; (8) text-only regression → 200
- [x] T025 [P] Update `specs/018-multimodal-image-support/contracts/chat-completions-multimodal.md` error response examples to use OpenAI error envelope format in all error cases (supersedes initial platform-schema draft)
- [x] T026 Run `ruff check services/guardrails/` — zero warnings; fix any issues in `services/guardrails/vision.py` and `services/guardrails/main.py`
- [x] T027 Run `mypy services/guardrails/ --ignore-missing-imports` — zero errors; add missing type annotations to all function signatures in `services/guardrails/vision.py`
- [x] T028 Run `make smoke` with `safety` profile active — confirm all pre-existing cases pass and new multimodal cases (AC-1 URL image, AC-3 non-vision rejection) pass; verify no regression on text-only completions (AC-6)

---

## Dependencies & Execution Order

### Phase Dependencies

```
T001 (ADR) — BLOCKS everything
    │
    ▼
T002–T008 (Foundational) — BLOCKS all user stories
    │
    ├──► T009–T013 (US1: URL image) — independently startable
    │
    ├──► T014–T018 (US2: base64) — can start after US1 T011 establishes validate_vision_request()
    │
    ├──► T019–T021 (US3: rejection) — can start after Foundational; benefits from US1/US2 complete
    │
    └──► T022–T023 (US4: multi-image) — can start after US1 + US2 complete
         │
         ▼
    T024–T028 (Polish) — after all desired stories complete
```

### User Story Dependencies

- **US1 (P1)**: Starts after Phase 2. No dependency on US2/US3/US4.
- **US2 (P1)**: Starts after Phase 2. Adds to `validate_vision_request()` established in US1 — best started after T011 is done.
- **US3 (P2)**: Starts after Phase 2. Model rejection path is a separate code branch — can overlap with US2.
- **US4 (P3)**: Depends on US1 + US2 complete (multi-image validation builds on both).

### Within Each Phase

- Tasks marked `[P]` touch distinct files or independent functions — safe to run in parallel.
- T006 → T007 are sequential (T007 wires T006 into main.py).
- T011 → T012 are sequential (T012 wires T011 into the proxy).
- T014–T018 are all extensions to the same `validate_vision_request()` function — run sequentially within Phase 4.

---

## Parallel Opportunities

```bash
# Phase 2 — all config tasks are parallel (different YAML stanzas):
T002 + T003 + T004 + T005 — LiteLLM config changes (same file, non-conflicting entries)
T006 — vision.py creation (new file, independent)

# Phase 3 — US1 setup tasks are parallel:
T009 + T010 — has_image_parts() and inject_detail_defaults() (independent functions)

# Phase 4 — US2 validation rules are parallel:
T014 + T017 + T018 — base64 format, count, detail checks (independent validation branches)

# Phase 7 — polish tasks are parallel:
T024 + T025 — contract tests and contract doc update (different files)
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1: T001 (ADR)
2. Complete Phase 2: T002–T008 (Foundational)
3. Complete Phase 3: T009–T013 (US1 — URL image)
4. **STOP and VALIDATE**: `curl` with URL image → HTTP 200 with description
5. Ship if valuable on its own

### Incremental Delivery

1. Phase 1 + 2 → Foundation ready
2. Phase 3 (US1) → URL images work → Demo/Deploy
3. Phase 4 (US2) → Base64 images work → Demo/Deploy
4. Phase 5 (US3) → Rejection path verified → Demo/Deploy
5. Phase 6 (US4) → Multi-image works → Demo/Deploy
6. Phase 7 → Polish, tests, lint clean

---

## Notes

- **T001 is a hard prerequisite** — constitution §4.4 amendment must be reviewed and merged before any code is written.
- `[P]` tasks touch distinct code paths; verify no merge conflicts on `vision.py` when running Phase 4 tasks in parallel.
- `MAX_IMAGES_PER_REQUEST` env var on the Guardrails container controls the image count limit (default `5`); no code change needed to reconfigure.
- Commit after each checkpoint (end of Phase 2, end of each US phase) to enable bisect if regressions appear.
- `make smoke` must pass at each checkpoint — do not proceed to the next phase if smoke fails.
