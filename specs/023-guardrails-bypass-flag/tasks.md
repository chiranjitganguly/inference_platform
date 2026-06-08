# Tasks: Guardrails Bypass Flag

**Input**: Design documents from `specs/023-guardrails-bypass-flag/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/ ✅ | quickstart.md ✅

**Organization**: Tasks grouped by phase — each phase is independently testable.

---

## Phase 1: Core — Bypass flag extraction and routing

**Purpose**: Parse the `guardrails` flag, strip it from the forwarded body, and route to the correct path.

- [x] T001 In `services/guardrails/main.py` → `proxy()`: after `body = await request.body()`, add flag extraction block — parse body JSON, pop `guardrails` key if present (only for `POST /v1/chat/completions`), set `_guardrails_on: bool`, re-serialise body without the key; default `True` on any parse failure
- [x] T002 Move the `headers = {k: v ...}` dict comprehension from its original position (after the validation block) to immediately after the flag extraction block — this makes `headers` available to both the bypass and validation paths

---

## Phase 2: Streaming passthrough helper

**Purpose**: Implement `_streaming_passthrough()` for true SSE chunk forwarding.

- [x] T003 Add `async def _streaming_passthrough(request, body, headers, path) -> StreamingResponse` to `services/guardrails/main.py` — placed after `_stream_bytes()`:
  - `await _client.__aenter__()` to open the httpx client
  - `_stream_ctx = _client.stream(method, url, headers, content, params)`
  - `_upstream = await _stream_ctx.__aenter__()` — captures `status_code` and `headers` immediately
  - On exception after client enter: call `_client.__aexit__(None, None, None)` then re-raise
  - Call `_write_audit(request, body, _upstream.status_code)` while status is known
  - Build `resp_headers` from upstream headers, excluding `content-length` and `transfer-encoding`
  - Define inner `async def _gen()`: `async for chunk in _upstream.aiter_bytes(): yield chunk`; `finally: await _stream_ctx.__aexit__(...); await _client.__aexit__(...)`
  - Return `StreamingResponse(_gen(), status_code=..., headers=..., media_type="text/event-stream")`

---

## Phase 3: Non-streaming bypass and proxy() routing

**Purpose**: Wire bypass paths into `proxy()` and preserve the guardrails-on path unchanged.

- [x] T004 In `proxy()`: after `headers = {...}`, add `if not _guardrails_on:` block:
  - Parse `stream` flag from body (default `False` on parse failure)
  - If streaming: `return await _streaming_passthrough(request, body, headers, path)`
  - If not streaming: `async with httpx.AsyncClient(...) as _c: _up = await _c.request(...)`; `_write_audit(request, body, _up.status_code)`; `return Response(content=_up.content, status_code=_up.status_code, headers=dict(_up.headers))`
- [x] T005 Verify the guardrails-on path (`if path == "v1/embeddings"...` and the chat completions validation block) is unchanged and uses the moved `headers` variable correctly — no duplicate `headers` dict computation

---

## Phase 4: Reference client

**Purpose**: Provide a working example of the streaming bypass in `app/test.py`.

- [x] T006 Create `app/test.py`:
  - Imports: `asyncio`, `json`, `os`, `pathlib.Path`, `httpx`, `dotenv.load_dotenv`
  - `load_dotenv(Path(__file__).resolve().parents[1] / ".env")` — loads `PLATFORM_API_KEY`
  - `ENDPOINT = "http://localhost:8080/v1/chat/completions"`
  - `async def main()`: build headers and payload (`guardrails: False, stream: True`)
  - `async with httpx.AsyncClient(timeout=60)` + `client.stream("POST", ENDPOINT, ...)` context managers
  - `async for line in response.aiter_lines()`: filter `data:` lines, parse JSON, check for `"error"` key first (print and break), then extract `choices[0].delta.content` safely
  - `asyncio.run(main())`
- [x] T007 Add `PLATFORM_API_KEY=smoke-test-key-dev` to `.env` and `PLATFORM_API_KEY=` (name only) to `.env.example`

---

## Phase 5: Bug fixes applied during feature validation

**Purpose**: Defects discovered while validating the streaming passthrough end-to-end.

- [x] T008 [BUG-022-A] `docker-compose.yml` portal-backend service missing `REDIS_URL` env var — was defaulting to `redis://redis:6379` (non-existent host); fixed to `redis://redis-cache:6379`. Also added `redis-cache: condition: service_healthy` to `depends_on` to prevent startup ordering race.
- [x] T009 [BUG-022-B] `scripts/seed-kong.sh` missing registration of `DELETE /cache/flush` route — it existed in `kong.yml` but was not in the seed script's `create_admin_services()` function. Added `curl -sf -X PUT ${KONG_ADMIN}/services/portal-backend/routes/cache-flush ...` with `paths[]=/cache/flush`, `methods[]=DELETE`, `strip_path=false`.

---

## Phase 6: Polish & validation

- [x] T010 Verify ruff check passes on `services/guardrails/main.py` — no new warnings
- [x] T011 Verify mypy passes on `services/guardrails/main.py --ignore-missing-imports` — no type errors
- [x] T012 Validate SC-001: run `app/test.py` with timestamp logging — confirm multiple `data:` chunks at distinct timestamps for a 200+ token response
- [x] T013 Validate SC-003: run existing feature 018 (vision), 019 (function calling), 020 (structured output) smoke tests — no regressions
- [x] T014 Validate SC-004: Loki query `{service="guardrails"} |= "inference_request"` returns entries for bypass requests

---

## Dependencies & Execution Order

- Phase 1 → Phase 2 (T003 calls `_write_audit`, which is defined later in file — fine at runtime)
- Phase 2 → Phase 3 (T004 calls `_streaming_passthrough`)
- Phase 3 and Phase 4 are independent of each other
- Phase 5 (bug fixes) are independent — discovered during Phase 3 validation
- Phase 6 depends on all prior phases
