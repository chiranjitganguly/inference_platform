# Research: Guardrails Bypass Flag

## Decision 1: Where to implement the bypass flag

**Options considered**:
- A. In Kong (header-based routing to a different upstream)
- B. In the guardrails service (flag in request body)
- C. As a separate endpoint path (e.g. `/v1/chat/completions/unsafe`)

**Decision**: B — guardrails service, flag in request body.

**Rationale**: Kong header-based routing (A) would require a second upstream service definition and diverges the architecture (Kong → LiteLLM bypasses guardrails entirely). A separate path (C) is a breaking change to the API surface and requires Kong config changes. Flag in body (B) keeps the Kong → Guardrails → LiteLLM chain intact; the guardrails service is the correct layer since it owns the validation gates.

---

## Decision 2: How to implement true streaming passthrough

**Options considered**:
- A. `await client.request()` then check if streaming and stream buffered body — current approach for standard path
- B. `async with client.stream()` inside the generator — can't get status code before returning `StreamingResponse`
- C. `__aenter__` / `__aexit__` manually — get status code synchronously from response headers, keep connection open via generator closure

**Decision**: C — manual `__aenter__` / `__aexit__`.

**Rationale**: Option A is what the standard path does — it buffers the full response before detecting streaming, which defeats the purpose. Option B is architecturally cleaner but cannot provide `status_code` to `StreamingResponse` before the generator starts. Option C gets the HTTP response headers (including status) immediately after entering the stream context, then yields body bytes from an async generator that keeps the context managers alive via a `finally` block.

---

## Decision 3: Error handling when `__aenter__` raises

If `_client.__aenter__()` succeeds but `_stream_ctx.__aenter__()` raises (connection refused, timeout), the httpx client must be closed to avoid leaking the connection. The implementation wraps `_stream_ctx.__aenter__()` in `try/except` and calls `_client.__aexit__(None, None, None)` before re-raising. The exception propagates to FastAPI's exception handler and returns HTTP 500.

---

## Decision 4: Flag stripping — pop vs. reconstruct

**Options considered**:
- A. `del payload["guardrails"]` then re-serialise
- B. `payload.pop("guardrails")` then re-serialise
- C. Filter at serialisation time with a field exclusion set

**Decision**: B — `dict.pop()` with `json.dumps(payload).encode()`.

**Rationale**: Minimal, readable, idiomatic Python. The body bytes are always re-serialised when the flag is present, so field ordering may differ slightly from the original — this is acceptable since JSON object field order is not significant and LiteLLM does not validate ordering.

---

## Decision 5: Default when `guardrails` key is absent

**Decision**: Default to `True` (guardrails on) when the key is absent or when the body is not valid JSON.

**Rationale**: Safe default — always validate unless explicitly told not to. A malformed body that prevents parsing the flag is treated conservatively; validation gates may then reject the malformed body with a clearer error than a downstream LiteLLM 400.

---

## Decision 6: Reference client implementation — httpx vs. openai SDK

**Options considered**:
- A. `openai.AsyncOpenAI` with `base_url` pointing to Kong
- B. `httpx.AsyncClient` calling the endpoint explicitly

**Decision**: B — httpx with explicit `ENDPOINT = "http://localhost:8080/v1/chat/completions"`.

**Rationale**: The openai SDK hides the URL construction (`base_url + /chat/completions`) and sends `Authorization: Bearer <key>` which conflicts with Kong's raw key-auth expectation. httpx makes the endpoint URL visible in code and gives full control over headers, aligning with the user's requirement that the streaming endpoint be explicit in the client code.

---

## Decision 7: Kong key-auth format — raw key vs. Bearer token

Kong's key-auth plugin is configured with `key_names: ["Authorization"]`. It matches the raw header value against stored consumer keys. The stored key is `smoke-test-key-dev` — so the `Authorization` header must be exactly `smoke-test-key-dev`, not `Bearer smoke-test-key-dev`.

This differs from the LiteLLM master key path (portal-backend), which calls `authorization.removeprefix("Bearer ").strip()` and accepts both formats. For Kong-authenticated endpoints (inference, cache-flush), callers must send the raw key without `Bearer`.
