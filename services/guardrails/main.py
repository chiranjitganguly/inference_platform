"""
Guardrails service — the middle layer in the Kong → Guardrails → LiteLLM request chain.

This service sits between Kong (the API gateway) and LiteLLM (the model proxy). Every
inference request passes through it. Its responsibilities are:

  1. Vision validation      : pre-proxy gate for multimodal image requests (vision.py).
  2. Function-calling       : pre-proxy gate for tool/function requests (function_calling.py)
                              AND post-proxy response validation (FR-016).
  3. Structured output      : pre-proxy gate for json_schema requests (structured_output.py)
                              AND post-proxy retry loop guaranteeing schema-conforming JSON.
  4. Embeddings metadata    : injects no_log=True so Phoenix/Langfuse skip embedding traces.
  5. 503 normalisation      : rewrites LiteLLM's fallback-exhausted error to the platform schema.
  6. Audit logging          : writes a metadata-only structured log entry for every request
                              (no prompt content, no image data, no tool arguments,
                              no schema content — constitution §II).

Request flow through this file:
  proxy()
    ├── _inject_no_log()              [embeddings only]
    ├── _validate_vision()            [chat/completions with image parts]
    ├── _validate_function_calling()  [chat/completions with tools]
    ├── _validate_structured_output() [chat/completions with response_format.type=json_schema]
    ├── upstream HTTP call (LiteLLM) — with retry loop for structured output requests
    ├── _write_audit()
    ├── _normalise_503()              [on 503 from LiteLLM]
    └── validate_tool_call_response() [on 200 when tools were present, non-SO only]

All validation modules are imported with a try/except to support both package imports
(local dev / tests) and flat imports (Docker CWD=/app).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

try:
    from .vision import (  # package import (local dev / tests)
        VisionCapabilityCache,
        count_image_parts,
        has_image_parts,
        inject_detail_defaults,
        validate_vision_request,
    )
    from .function_calling import (
        FunctionCallingCapabilityCache,
        count_tools,
        has_tools,
        validate_function_calling_request,
        validate_tool_call_response,
    )
    from .structured_output import (
        StructuredOutputCapabilityCache,
        compute_schema_hash,
        has_structured_output_request,
        inject_schema_metadata,
        validate_structured_output_request,
        validate_structured_output_response,
    )
except ImportError:
    from vision import (  # flat import (Docker — CWD is /app)  # noqa: PLC0415
        VisionCapabilityCache,
        count_image_parts,
        has_image_parts,
        inject_detail_defaults,
        validate_vision_request,
    )
    from function_calling import (  # noqa: PLC0415
        FunctionCallingCapabilityCache,
        count_tools,
        has_tools,
        validate_function_calling_request,
        validate_tool_call_response,
    )
    from structured_output import (  # noqa: PLC0415
        StructuredOutputCapabilityCache,
        compute_schema_hash,
        has_structured_output_request,
        inject_schema_metadata,
        validate_structured_output_request,
        validate_structured_output_response,
    )

logger = logging.getLogger("guardrails")
audit = logging.getLogger("guardrails.audit")

LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
MAX_SO_RETRIES: int = int(os.environ.get("STRUCTURED_OUTPUT_MAX_RETRIES", "3"))


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    """
    FastAPI lifespan context manager — loads all capability caches at startup.

    Why needed: FastAPI's lifespan replaces the deprecated @app.on_event("startup")
    pattern. It runs once before the app begins accepting requests, ensuring both
    capability caches are populated before any request handler can access them via
    app.state.

    What it does:
      1. Creates and calls VisionCapabilityCache.load() → stored as app.state.vision_cache.
      2. Creates and calls FunctionCallingCapabilityCache.load() → stored as app.state.fc_cache.
      3. Yields control to the app (requests are served during the yield).

    Why fail-fast: If either cache cannot reach LiteLLM, load() raises RuntimeError.
    The lifespan propagates this exception, preventing the app from starting in a
    broken state where it would silently pass all image/tool requests through
    without capability gating.

    Relationship to other functions:
    - _validate_vision() reads app.state.vision_cache on every multimodal request.
    - _validate_function_calling() reads app.state.fc_cache on every tools request.
    - Both caches call the same LiteLLM /model/info endpoint independently.

    Args:
        application: The FastAPI app instance. State is attached to this object.

    Yields: None. The app serves requests between entry and exit of this context.
    """
    vision_cache = VisionCapabilityCache()
    await vision_cache.load()
    application.state.vision_cache = vision_cache
    logger.info(
        "VisionCapabilityCache loaded: %d vision models", len(vision_cache.vision_model_names)
    )

    fc_cache = FunctionCallingCapabilityCache()
    await fc_cache.load()
    application.state.fc_cache = fc_cache
    logger.info(
        "FunctionCallingCapabilityCache loaded: %d function-capable models",
        len(fc_cache.fc_model_names),
    )

    so_cache = StructuredOutputCapabilityCache()
    await so_cache.load()
    application.state.so_cache = so_cache
    logger.info(
        "StructuredOutputCapabilityCache loaded: %d native, %d prompt-based models",
        len(so_cache.native_model_names),
        len(so_cache.prompt_model_names),
    )

    # WebSocket connection tracking (feature 021)
    application.state.ws_connections: dict[str, int] = {}
    application.state.ws_lock = asyncio.Lock()
    yield


app = FastAPI(title="Guardrails Service", version="0.3.0", lifespan=_lifespan)


async def _stream_bytes(response: httpx.Response) -> AsyncIterator[bytes]:
    """
    Async generator that yields raw bytes from an httpx streaming response.

    Why needed: FastAPI's StreamingResponse requires an async iterable of bytes.
    httpx.Response.aiter_bytes() provides that, but it must be wrapped in a
    generator function so the httpx response object stays open during streaming.

    Relationship to other functions:
    - Used exclusively by proxy() when the upstream LiteLLM response has
      Content-Type: text/event-stream (i.e., the caller requested stream=true).
    - Not used for function-calling or vision responses because both are
      non-streaming by enforced policy (stream=true is rejected for those paths).

    Args:
        response: An open httpx.Response that was received with streaming enabled.

    Yields:
        Raw byte chunks as they arrive from the upstream LiteLLM response.
    """
    async for chunk in response.aiter_bytes():
        yield chunk


async def _streaming_passthrough(
    request: Request, body: bytes, headers: dict[str, str], path: str
) -> StreamingResponse:
    """
    True chunk-by-chunk streaming passthrough used when guardrails=False.

    Uses httpx client.stream() so the upstream connection stays open and SSE
    chunks are forwarded to the caller as they arrive, without buffering the
    full response. The httpx client and stream context are kept alive for the
    lifetime of the FastAPI StreamingResponse via the _gen closure's finally block.

    The response status code and headers are captured immediately after the HTTP
    response headers arrive (before the body is read), so StreamingResponse is
    constructed with the correct upstream status.

    Args:
        request: Incoming FastAPI request (method, query params).
        body:    Request body bytes with the guardrails flag already stripped.
        headers: Forwarding headers (host/content-length/transfer-encoding removed).
        path:    URL path after the host, e.g. "v1/chat/completions".

    Returns:
        A StreamingResponse that forwards SSE chunks from LiteLLM in real time.
    """
    _client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
    await _client.__aenter__()
    try:
        _stream_ctx = _client.stream(
            method=request.method,
            url=f"{LITELLM_BASE_URL}/{path}",
            headers=headers,
            content=body,
            params=dict(request.query_params),
        )
        _upstream: httpx.Response = await _stream_ctx.__aenter__()
    except Exception:
        await _client.__aexit__(None, None, None)
        raise

    _write_audit(request, body, _upstream.status_code)

    resp_headers = {
        k: v for k, v in _upstream.headers.items()
        if k.lower() not in ("content-length", "transfer-encoding")
    }

    async def _gen() -> AsyncIterator[bytes]:
        try:
            async for chunk in _upstream.aiter_bytes():
                yield chunk
        finally:
            await _stream_ctx.__aexit__(None, None, None)
            await _client.__aexit__(None, None, None)

    return StreamingResponse(
        _gen(),
        status_code=_upstream.status_code,
        headers=resp_headers,
        media_type="text/event-stream",
    )


def _inject_no_log(body: bytes) -> bytes:
    """
    Inject metadata.no_log=True into an embeddings request body.

    Why needed: LiteLLM emits callbacks to Phoenix Arize and Langfuse for every
    request. For embeddings, those traces add noise without adding value (embeddings
    are not LLM quality signals) and would inflate Phoenix span counts. Setting
    no_log=True in the request metadata tells LiteLLM to suppress callbacks for
    this specific request.

    This function is safe to call even when LiteLLM callbacks are disabled
    (the metadata field is silently ignored in that state).

    Relationship to other functions:
    - Called by proxy() only for POST /v1/embeddings requests, before the upstream
      call is made.
    - Has no dependency on any capability cache or validation logic.

    Args:
        body: Raw request body bytes. Expected to be valid JSON; if parsing fails
              the original body is returned unchanged (safe degradation).

    Returns:
        Modified body bytes with metadata.no_log set to "True", or the original
        body bytes if the body is not valid JSON.
    """
    try:
        payload: dict[str, Any] = json.loads(body)
    except Exception:
        return body
    payload.setdefault("metadata", {})["no_log"] = "True"
    return json.dumps(payload).encode()


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def proxy(request: Request, path: str) -> Response:
    """
    Catch-all reverse proxy — the single entry point for all inbound traffic.

    Why needed: The Guardrails service sits between Kong and LiteLLM (constitution §2.1).
    Every request that Kong forwards to port 8088 lands here. This function applies
    all applicable validation gates and then proxies the request to LiteLLM, making
    the service transparent to callers for requests that pass validation.

    Request flow:
      1. Embeddings: inject no_log metadata, then proxy.
      2. Chat completions: run vision gate → run function-calling gate → proxy →
         run post-proxy FC response validation → return to caller.
      3. All other paths: proxy directly with no additional processing.

    Post-proxy behaviour (chat completions with tools):
      After receiving an HTTP 200 from LiteLLM, the response body is parsed and
      passed to validate_tool_call_response(). If any tool call arguments contain
      invalid JSON, a 502 is returned instead of the upstream response. This
      ensures callers always receive parseable arguments (FR-016).

    Relationship to other functions:
    - Calls _inject_no_log() for embeddings.
    - Calls _validate_vision() and _validate_function_calling() for chat completions.
    - Calls _write_audit() after every upstream response (pass or fail).
    - Calls _normalise_503() when LiteLLM returns 503.
    - Calls validate_tool_call_response() (from function_calling.py) post-proxy.
    - Uses _stream_bytes() for SSE streaming responses.

    Args:
        request: The incoming FastAPI Request object (headers, body, query params).
        path:    URL path after the host, e.g. "v1/chat/completions".

    Returns:
        A FastAPI Response (or StreamingResponse for SSE). The status code and
        body come from LiteLLM unless a validation gate short-circuits earlier.
    """
    body = await request.body()

    # Extract and strip the guardrails flag before forwarding to LiteLLM.
    # guardrails=False → skip all validation gates, passthrough directly.
    # guardrails=True (default) → run all validation as normal.
    _guardrails_on = True
    if path == "v1/chat/completions" and request.method == "POST":
        try:
            _pjson: dict[str, Any] = json.loads(body)
            if "guardrails" in _pjson:
                _guardrails_on = bool(_pjson.pop("guardrails"))
                body = json.dumps(_pjson).encode()
        except Exception:
            pass

    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "transfer-encoding")
    }

    if not _guardrails_on:
        try:
            _is_streaming = bool(json.loads(body).get("stream", False))
        except Exception:
            _is_streaming = False

        if _is_streaming:
            return await _streaming_passthrough(request, body, headers, path)

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as _c:
            _up = await _c.request(
                method=request.method,
                url=f"{LITELLM_BASE_URL}/{path}",
                headers=headers,
                content=body,
                params=dict(request.query_params),
            )
        _write_audit(request, body, _up.status_code)
        return Response(
            content=_up.content,
            status_code=_up.status_code,
            headers=dict(_up.headers),
        )

    if path == "v1/embeddings" and request.method == "POST":
        body = _inject_no_log(body)

    image_part_count = 0
    tool_count = 0
    _had_tools = False
    _so_schema_name = ""
    _so_schema: dict[str, Any] = {}
    _so_schema_hash = ""
    _had_so = False

    if path == "v1/chat/completions" and request.method == "POST":
        result = await _validate_vision(body, request)
        if isinstance(result, Response):
            return result
        body, image_part_count = result

        fc_result = await _validate_function_calling(body, request)
        if isinstance(fc_result, Response):
            return fc_result
        body, tool_count = fc_result
        _had_tools = tool_count > 0

        so_gate = await _validate_structured_output(body, request)
        if isinstance(so_gate, Response):
            return so_gate
        body, _so_schema_name, _so_schema, _so_schema_hash = so_gate
        _had_so = bool(_so_schema_name)

    so_retry_count = 0

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        if _had_so:
            # Retry loop: attempt up to MAX_SO_RETRIES + 1 total calls.
            # On each attempt, validate choices[0].message.content against the schema.
            # Break on pass or non-200; return 422 after retries are exhausted.
            upstream = None
            for attempt in range(MAX_SO_RETRIES + 1):
                upstream = await client.request(
                    method=request.method,
                    url=f"{LITELLM_BASE_URL}/{path}",
                    headers=headers,
                    content=body,
                    params=dict(request.query_params),
                )
                if upstream.status_code != 200:
                    break
                try:
                    resp_json_so: dict[str, Any] = json.loads(upstream.content)
                except Exception:
                    break  # non-JSON 200 — pass through unchanged
                so_check = validate_structured_output_response(resp_json_so, _so_schema)
                if so_check == "pass":
                    break
                # "retry" — schema mismatch; exhaust budget or loop again
                if attempt == MAX_SO_RETRIES:
                    conformance_error: dict[str, Any] = {
                        "error": {
                            "message": (
                                f"Model response did not conform to the provided JSON schema "
                                f"after {MAX_SO_RETRIES + 1} attempt(s)."
                            ),
                            "type": "schema_conformance_failure",
                            "code": "schema_conformance_failure",
                        },
                        "retry_count": MAX_SO_RETRIES,
                        "schema_name": _so_schema_name,
                    }
                    _write_audit(
                        request, body, 422,
                        schema_name=_so_schema_name,
                        so_retry_count=MAX_SO_RETRIES,
                    )
                    return Response(
                        content=json.dumps(conformance_error),
                        status_code=422,
                        media_type="application/json",
                    )
                so_retry_count = attempt + 1
        else:
            upstream = await client.request(
                method=request.method,
                url=f"{LITELLM_BASE_URL}/{path}",
                headers=headers,
                content=body,
                params=dict(request.query_params),
            )

    assert upstream is not None  # always set: either branch executes at least once

    _write_audit(
        request, body, upstream.status_code,
        image_part_count, tool_count,
        schema_name=_so_schema_name,
        so_retry_count=so_retry_count,
    )

    if upstream.status_code == 503:
        return _normalise_503(upstream, body)

    is_streaming = "text/event-stream" in upstream.headers.get("content-type", "")
    if is_streaming:
        return StreamingResponse(
            _stream_bytes(upstream),
            status_code=upstream.status_code,
            headers=dict(upstream.headers),
            media_type="text/event-stream",
        )

    # Post-proxy: validate tool_call response arguments (FR-016). Not applied to SO
    # requests since structured output and function calling are mutually exclusive.
    if _had_tools and not _had_so and upstream.status_code == 200:
        try:
            resp_json: dict[str, Any] = json.loads(upstream.content)
            fc_resp_error = validate_tool_call_response(resp_json)
            if fc_resp_error is not None:
                error_body, error_status = fc_resp_error
                return Response(
                    content=json.dumps(error_body),
                    status_code=error_status,
                    media_type="application/json",
                )
        except Exception:
            pass  # non-JSON upstream response — pass through unchanged

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=dict(upstream.headers),
    )


async def _validate_vision(
    body: bytes, request: Request
) -> tuple[bytes, int] | Response:
    """
    Pre-proxy gate: run vision validation for chat completion requests with image parts.

    Why needed: Separating the gate into its own async function keeps proxy() readable
    and makes the vision validation path independently testable. It bridges between
    the HTTP layer (raw bytes, FastAPI Request) and the pure validation logic in
    vision.py (dicts, frozensets).

    Flow:
      1. Parse the body bytes as JSON. If parsing fails, return body unchanged
         (the upstream will handle malformed JSON with its own error).
      2. Check has_image_parts(); if no images, return immediately (zero cost).
      3. Read app.state.vision_cache, call validate_vision_request().
      4. On failure: return a Response immediately (proxy() returns this to caller).
      5. On success: call inject_detail_defaults() to set detail="auto" where absent,
         re-serialise the modified body, and return (new_body, image_count).

    Relationship to other functions:
    - Called by proxy() for every POST /v1/chat/completions request.
    - Runs BEFORE _validate_function_calling() so vision errors are surfaced first.
    - Uses VisionCapabilityCache from app.state (loaded by _lifespan()).
    - Delegates all business logic to validate_vision_request() and
      inject_detail_defaults() in vision.py.

    Args:
        body:    Raw request body bytes (JSON-encoded chat completion request).
        request: FastAPI Request, used to access app.state.vision_cache.

    Returns:
        On success: 2-tuple of (body_bytes, image_part_count). body_bytes may differ
            from the input if detail defaults were injected.
        On validation failure: a FastAPI Response with the appropriate HTTP status
            and OpenAI error envelope body. proxy() returns this directly to the caller.
    """
    try:
        payload: dict[str, Any] = json.loads(body)
    except Exception:
        return body, 0

    messages = payload.get("messages", [])
    if not has_image_parts(messages):
        return body, 0

    vision_cache: VisionCapabilityCache = request.app.state.vision_cache
    error_result = validate_vision_request(payload, vision_cache.vision_model_names)
    if error_result is not None:
        error_body, status_code = error_result
        return Response(
            content=json.dumps(error_body),
            status_code=status_code,
            media_type="application/json",
        )

    image_count = count_image_parts(messages)
    payload["messages"] = inject_detail_defaults(messages)
    return json.dumps(payload).encode(), image_count


async def _validate_function_calling(
    body: bytes, request: Request
) -> tuple[bytes, int] | Response:
    """
    Pre-proxy gate: run function-calling validation for chat completion requests with tools.

    Why needed: Mirrors _validate_vision() for the function-calling feature. Bridges
    between the HTTP layer and the pure validation logic in function_calling.py. Also
    determines whether the post-proxy response validation step (validate_tool_call_response)
    should fire by returning a non-zero tool_count on success.

    Flow:
      1. Parse the body bytes as JSON. On failure, return body unchanged.
      2. Determine whether to enter validation: enter if tools is non-empty OR if
         tool_choice is an explicit object (handles the edge case where tools=[] but
         tool_choice is set — validated by rule 3 in validate_function_calling_request).
      3. If neither condition holds, return immediately (zero cost for non-tool requests).
      4. Read app.state.fc_cache, call validate_function_calling_request().
      5. On failure: return a Response immediately (proxy() returns this to caller).
      6. On success: return (original_body, tool_count). Unlike vision, no body
         modification is needed (there are no defaults to inject for function calling).

    Relationship to other functions:
    - Called by proxy() for every POST /v1/chat/completions request, AFTER
      _validate_vision() has already run.
    - The tool_count it returns is stored in _had_tools by proxy(). proxy() uses
      _had_tools to decide whether to run validate_tool_call_response() post-proxy.
    - Uses FunctionCallingCapabilityCache from app.state (loaded by _lifespan()).
    - Delegates all business logic to validate_function_calling_request() in
      function_calling.py.

    Args:
        body:    Raw request body bytes (JSON-encoded chat completion request).
        request: FastAPI Request, used to access app.state.fc_cache.

    Returns:
        On success: 2-tuple of (body_bytes, tool_count). body_bytes is identical to
            the input (no modification needed for function calling).
            tool_count is 0 for non-tool requests; >= 1 for tool requests.
        On validation failure: a FastAPI Response with the appropriate HTTP status
            and OpenAI error envelope body. proxy() returns this directly to the caller.
    """
    try:
        payload: dict[str, Any] = json.loads(body)
    except Exception:
        return body, 0

    tool_choice = payload.get("tool_choice")
    _has_explicit_tc = tool_choice is not None and tool_choice not in ("auto", "none")
    if not has_tools(payload) and not _has_explicit_tc:
        return body, 0

    fc_cache: FunctionCallingCapabilityCache = request.app.state.fc_cache
    error_result = validate_function_calling_request(payload, fc_cache.fc_model_names)
    if error_result is not None:
        error_body, status_code = error_result
        return Response(
            content=json.dumps(error_body),
            status_code=status_code,
            media_type="application/json",
        )

    return body, count_tools(payload)


async def _validate_structured_output(
    body: bytes, request: Request
) -> tuple[bytes, str, dict[str, Any], str] | Response:
    """
    Pre-proxy gate: validate and prepare structured output requests.

    Bridges between the HTTP layer (raw bytes, FastAPI Request) and the pure
    validation logic in structured_output.py. Mirrors _validate_vision() and
    _validate_function_calling() in structure.

    Flow:
      1. Parse body as JSON. On failure, return (body, "", {}, "") — non-SO path.
      2. has_structured_output_request(): if False, return immediately (zero cost).
      3. validate_structured_output_request(): streaming guard, field checks, model
         cap gate, schema self-validity. On failure, return a 4xx/422 Response.
      4. On success: extract schema_name, schema, compute hash, inject metadata.

    Returns:
      On success: (new_body_bytes, schema_name, schema_dict, schema_hash)
                  schema_name is empty string for non-SO requests.
      On validation failure: a FastAPI Response with the error body. proxy() returns
                             this directly to the caller.
    """
    try:
        payload: dict[str, Any] = json.loads(body)
    except Exception:
        return body, "", {}, ""

    if not has_structured_output_request(payload):
        return body, "", {}, ""

    so_cache: StructuredOutputCapabilityCache = request.app.state.so_cache
    error_result = validate_structured_output_request(payload, so_cache)
    if error_result is not None:
        error_body, status_code = error_result
        return Response(
            content=json.dumps(error_body),
            status_code=status_code,
            media_type="application/json",
        )

    rf: dict[str, Any] = payload.get("response_format", {})
    schema_name: str = rf.get("name", "")
    schema: dict[str, Any] = rf.get("schema", {})
    schema_hash = compute_schema_hash(schema)
    new_payload = inject_schema_metadata(payload, schema_name, schema_hash)
    # OpenAI's API (and LiteLLM v1.52.0) requires the nested json_schema shape:
    #   {"type": "json_schema", "json_schema": {"name": ..., "strict": ..., "schema": ...}}
    # Our public contract accepts the flat shape (name/strict/schema at response_format level)
    # so we normalise here before forwarding to LiteLLM.
    new_payload["response_format"] = {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "strict": rf.get("strict", True),
            "schema": schema,
        },
    }
    return json.dumps(new_payload).encode(), schema_name, schema, schema_hash


def _write_audit(
    request: Request,
    body: bytes,
    status_code: int,
    image_part_count: int = 0,
    tool_count: int = 0,
    schema_name: str = "",
    so_retry_count: int = 0,
) -> None:
    """
    Write a structured, metadata-only audit log entry for every proxied request.

    Why needed: Constitution §II requires that prompt content, image data, and tool
    arguments are never persisted in any log or trace. The audit trail must exist for
    compliance (operators need to know a request happened) but must contain only
    metadata. This function enforces that boundary: it reads only safe fields from
    the request (key hash, model name, request ID) and never includes the messages
    array or any user-supplied content.

    The entry is emitted to the "guardrails.audit" logger, which Loki collects as
    the platform's immutable compliance audit trail (see constitution §6.3).

    Relationship to other functions:
    - Called by proxy() immediately after the upstream response is received,
      regardless of whether the response was a success or an error.
    - image_part_count is supplied by _validate_vision(); tool_count by
      _validate_function_calling(). Both default to 0 for non-multimodal requests.
    - The key_hash is a SHA-256 digest of the raw Authorization header value
      (not the decoded key). This lets operators correlate requests to a consumer
      without exposing the secret.

    Args:
        request:          The original FastAPI Request (used for headers).
        body:             The request body bytes, used only to extract "model".
                          Content (messages, tools, images, schema) is never read.
        status_code:      HTTP status code returned by the upstream LiteLLM response.
        image_part_count: Number of image_url parts in the request (0 for text-only).
        tool_count:       Number of tool definitions in the request (0 for non-tool requests).
        schema_name:      response_format.name for SO requests; "" for all others.
                          This is a caller-chosen identifier, not prompt content —
                          permitted in audit entries per constitution §6.3.
        so_retry_count:   Number of retry attempts that occurred for SO requests (0 = no
                          retries, either non-SO or first-attempt pass).

    Returns: None. Side effect: one JSON line emitted to the "guardrails.audit" logger.

    Audit entry fields:
        timestamp        ISO-8601 UTC timestamp of when the request was processed.
        event_type       Always "inference_request".
        request_id       Value of the X-Request-ID header (set by Kong).
        key_hash         SHA-256 of the Authorization header value; empty if absent.
        model_name       Value of the "model" field from the request body.
        pii_entity_count Always 0 (PII scanning is a future phase).
        scanner_blocked  Always False (content scanning is a future phase).
        image_part_count Count of image_url parts (0 for text-only requests).
        tool_count       Count of tool definitions (0 for non-function-calling requests).
        schema_name      Schema identifier for SO requests; "" otherwise.
        so_retry_count   Retry attempts for SO requests; 0 otherwise.
    """
    api_key: str = request.headers.get("authorization", "")
    key_hash = hashlib.sha256(api_key.encode()).hexdigest() if api_key else ""
    model_name = ""
    try:
        payload: dict[str, Any] = json.loads(body or b"{}")
        model_name = payload.get("model", "")
    except Exception:
        pass
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "inference_request",
        "request_id": request.headers.get("x-request-id", ""),
        "key_hash": key_hash,
        "model_name": model_name,
        "pii_entity_count": 0,
        "scanner_blocked": False,
        "image_part_count": image_part_count,
        "tool_count": tool_count,
        "schema_name": schema_name,
        "so_retry_count": so_retry_count,
    }
    audit.info(json.dumps(entry))


def _normalise_503(upstream: httpx.Response, request_body: bytes) -> Response:
    """
    Reformat LiteLLM's 503 response into the platform's structured error schema.

    Why needed: When LiteLLM exhausts all fallback models, it returns a 503 with
    its own internal error body format, which does not match the platform error
    schema ({"error": "...", "message": "...", "detail": {...}}). Callers should
    receive a consistent error shape regardless of which layer generated the error.

    Note: This function intentionally uses the platform schema (not the OpenAI
    error envelope used for validation rejections). 503 errors are infrastructure
    failures, not request validation failures, and they are emitted on all endpoint
    paths — not just /v1/chat/completions. ADR-018's OpenAI envelope scope is
    limited to vision and function-calling validation rejections on that one endpoint.

    Relationship to other functions:
    - Called by proxy() when upstream.status_code == 503.
    - Runs after _write_audit(), so the 503 is always recorded in the audit log.
    - Takes request_body to extract "model" for the "requested_model" detail field,
      helping operators identify which model chain exhausted.

    Args:
        upstream:     The 503 httpx.Response from LiteLLM. Its JSON body is parsed
                      to extract the original error message; if parsing fails a
                      generic message is used.
        request_body: The original request body bytes, used only to extract "model".

    Returns:
        A FastAPI Response with status 503 and a JSON body:
        {
          "error": "all_fallbacks_exhausted",
          "message": "<LiteLLM's original message or generic fallback>",
          "detail": {
            "requested_model": "<model name from request>",
            "models_attempted": [],
            "failure_reasons": {}
          }
        }
    """
    try:
        original = upstream.json()
        error_obj = original.get("error", {})
        message: str = (
            error_obj.get("message")
            if isinstance(error_obj, dict)
            else str(error_obj)
        ) or "All models in the fallback chain are unavailable."
    except Exception:
        message = "All models in the fallback chain are unavailable."

    try:
        req: dict[str, Any] = json.loads(request_body or b"{}")
    except Exception:
        req = {}

    body = {
        "error": "all_fallbacks_exhausted",
        "message": message,
        "detail": {
            "requested_model": req.get("model", "unknown"),
            "models_attempted": [],
            "failure_reasons": {},
        },
    }
    return Response(
        content=json.dumps(body),
        status_code=503,
        media_type="application/json",
    )


# ── WebSocket streaming — feature 021 ────────────────────────────────────────
# Import and register the WS handler after the app and all helpers are defined
# so the handler module can import _write_audit, _validate_*, etc. from this module.
try:
    from .websocket import ws_chat_completions  # package import (local dev / tests)
except ImportError:
    from websocket import ws_chat_completions  # type: ignore[no-redef]  # flat import (Docker)

app.add_api_websocket_route("/ws/v1/chat/completions", ws_chat_completions)
