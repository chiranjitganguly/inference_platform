"""
WebSocket streaming handler — feature 021.

Exposes /ws/v1/chat/completions as a persistent bidirectional endpoint.
Callers connect once, send OpenAI-format chat completion payloads as JSON frames,
and receive OpenAI streaming delta chunks forwarded from LiteLLM's HTTP SSE
endpoint, followed by a {"type": "stream_complete"} sentinel.

Request flow:
  Kong :8080 (key-auth, WS upgrade)
    → Guardrails :8088 ws_chat_completions()
        ├── acquire_connection()          connection-limit gate
        ├── websocket.accept()
        └── receive-loop
              ├── JSON parse + is_streaming guard
              ├── vision / function-calling / structured-output gates
              └── _stream_to_ws()         HTTP SSE → WS bridge to LiteLLM :4000

Constitution invariants maintained:
  - Full Kong → Guardrails → LiteLLM chain preserved (no shortcut).
  - No prompt content written to logs or spans (metadata only in audit entries).
  - OpenAI streaming chunk format forwarded verbatim to callers.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import WebSocket, WebSocketDisconnect
from opentelemetry import trace
from opentelemetry.trace import SpanKind

try:
    from .vision import (  # package import (local dev / tests)
        VisionCapabilityCache,
        has_image_parts,
        inject_detail_defaults,
        validate_vision_request,
    )
    from .function_calling import (
        FunctionCallingCapabilityCache,
        has_tools,
        validate_function_calling_request,
    )
    from .structured_output import (
        StructuredOutputCapabilityCache,
        compute_schema_hash,
        has_structured_output_request,
        inject_schema_metadata,
        validate_structured_output_request,
    )
except ImportError:
    from vision import (  # flat import (Docker — CWD is /app)  # noqa: PLC0415
        VisionCapabilityCache,  # type: ignore[no-redef]
        has_image_parts,  # type: ignore[no-redef]
        inject_detail_defaults,  # type: ignore[no-redef]
        validate_vision_request,  # type: ignore[no-redef]
    )
    from function_calling import (  # noqa: PLC0415
        FunctionCallingCapabilityCache,  # type: ignore[no-redef]
        has_tools,  # type: ignore[no-redef]
        validate_function_calling_request,  # type: ignore[no-redef]
    )
    from structured_output import (  # noqa: PLC0415
        StructuredOutputCapabilityCache,  # type: ignore[no-redef]
        compute_schema_hash,  # type: ignore[no-redef]
        has_structured_output_request,  # type: ignore[no-redef]
        inject_schema_metadata,  # type: ignore[no-redef]
        validate_structured_output_request,  # type: ignore[no-redef]
    )

# ── Constants ─────────────────────────────────────────────────────────────────

LITELLM_BASE_URL: str = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
WS_MAX_CONNECTIONS_PER_KEY: int = int(os.environ.get("WS_MAX_CONNECTIONS_PER_KEY", "10"))
WS_IDLE_TIMEOUT_SECONDS: float = float(os.environ.get("WS_IDLE_TIMEOUT_SECONDS", "300"))

logger = logging.getLogger("guardrails")
audit = logging.getLogger("guardrails.audit")
tracer = trace.get_tracer("guardrails.websocket")

# ── Connection manager ────────────────────────────────────────────────────────


async def acquire_connection(app_state: Any, consumer_id: str) -> bool:
    """Increment the open-connection counter for consumer_id.

    Returns True if accepted, False if the per-key limit is already reached.
    """
    async with app_state.ws_lock:
        current: int = app_state.ws_connections.get(consumer_id, 0)
        if current >= WS_MAX_CONNECTIONS_PER_KEY:
            return False
        app_state.ws_connections[consumer_id] = current + 1
        return True


async def release_connection(app_state: Any, consumer_id: str) -> None:
    """Decrement the open-connection counter for consumer_id (floor at 0)."""
    async with app_state.ws_lock:
        current: int = app_state.ws_connections.get(consumer_id, 0)
        if current <= 1:
            app_state.ws_connections.pop(consumer_id, None)
        else:
            app_state.ws_connections[consumer_id] = current - 1


# ── Wire-message helpers ──────────────────────────────────────────────────────


def _error_frame(
    error_type: str, message: str, detail: dict[str, Any] | None = None
) -> str:
    return json.dumps(
        {
            "type": error_type,
            "error": error_type,
            "message": message,
            "detail": detail or {},
        }
    )


# ── Audit logging ─────────────────────────────────────────────────────────────


def _write_ws_audit(
    consumer_id: str,
    request_id: str,
    model_name: str,
    status_code: int,
    stream_error: bool = False,
) -> None:
    """Write a metadata-only audit entry for one WS stream request.

    No prompt content is ever included (constitution §II).
    """
    key_hash = hashlib.sha256(consumer_id.encode()).hexdigest() if consumer_id else ""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "ws_stream_request",
        "request_id": request_id,
        "key_hash": key_hash,
        "model_name": model_name,
        "pii_entity_count": 0,
        "scanner_blocked": False,
        "stream_error": stream_error,
        "status_code": status_code,
    }
    audit.info(json.dumps(entry))


# ── Validation gates ──────────────────────────────────────────────────────────


async def _run_validation_gates(
    body: bytes,
    websocket: WebSocket,
) -> bytes | None:
    """Run vision, function-calling, and structured-output gates.

    Accesses capability caches directly from websocket.app.state (same state
    object populated by _lifespan() in main.py). Returns (possibly modified)
    body bytes on success, or None after sending a validation_error frame
    (caller should continue the receive loop without starting a stream).
    """
    app_state = websocket.app.state

    # ── Vision gate ───────────────────────────────────────────────────────────
    try:
        payload: dict[str, Any] = json.loads(body)
    except Exception:
        payload = {}

    messages = payload.get("messages", [])
    if has_image_parts(messages):
        vision_cache: VisionCapabilityCache = app_state.vision_cache
        error_result = validate_vision_request(payload, vision_cache.vision_model_names)
        if error_result is not None:
            err_body, _ = error_result
            msg = err_body.get("error", {}).get("message", "Vision validation failed.")
            await websocket.send_text(_error_frame("validation_error", msg))
            return None
        payload["messages"] = inject_detail_defaults(messages)
        body = json.dumps(payload).encode()

    # ── Function-calling gate ─────────────────────────────────────────────────
    try:
        payload = json.loads(body)
    except Exception:
        payload = {}

    tool_choice = payload.get("tool_choice")
    _has_explicit_tc = tool_choice is not None and tool_choice not in ("auto", "none")
    if has_tools(payload) or _has_explicit_tc:
        fc_cache: FunctionCallingCapabilityCache = app_state.fc_cache
        fc_error = validate_function_calling_request(payload, fc_cache.fc_model_names)
        if fc_error is not None:
            err_body, _ = fc_error
            msg = err_body.get("error", {}).get("message", "Function-calling validation failed.")
            await websocket.send_text(_error_frame("validation_error", msg))
            return None

    # ── Structured-output gate ────────────────────────────────────────────────
    try:
        payload = json.loads(body)
    except Exception:
        payload = {}

    if has_structured_output_request(payload):
        so_cache: StructuredOutputCapabilityCache = app_state.so_cache
        so_error = validate_structured_output_request(payload, so_cache)
        if so_error is not None:
            err_body, _ = so_error
            msg = err_body.get("error", {}).get("message", "Structured-output validation failed.")
            await websocket.send_text(_error_frame("validation_error", msg))
            return None
        # Normalise response_format to the nested json_schema shape LiteLLM expects
        rf: dict[str, Any] = payload.get("response_format", {})
        schema_name: str = rf.get("name", "")
        schema: dict[str, Any] = rf.get("schema", {})
        schema_hash = compute_schema_hash(schema)
        payload = inject_schema_metadata(payload, schema_name, schema_hash)
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": rf.get("strict", True),
                "schema": schema,
            },
        }
        body = json.dumps(payload).encode()

    return body


# ── SSE → WebSocket bridge ────────────────────────────────────────────────────


async def _stream_to_ws(
    websocket: WebSocket,
    body: bytes,
    headers: dict[str, str],
    request_id: str,
) -> tuple[int, bool]:
    """Forward one LiteLLM HTTP SSE stream to the caller over WebSocket.

    Forces stream=True in the request body, iterates the SSE event stream,
    and forwards each data: line as a discrete WebSocket JSON text frame.
    Sends {"type":"stream_complete"} after the [DONE] sentinel.

    Returns (http_status_code, had_error).
    """
    try:
        payload: dict[str, Any] = json.loads(body)
    except Exception:
        payload = {}
    payload["stream"] = True
    body = json.dumps(payload).encode()

    forward_headers = {
        k: v
        for k, v in headers.items()
        if k.lower() not in (
            "host", "content-length", "transfer-encoding",
            "upgrade", "connection", "sec-websocket-key",
            "sec-websocket-version", "sec-websocket-extensions",
        )
    }
    forward_headers["x-request-id"] = request_id

    status_code = 200
    had_error = False

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            async with client.stream(
                "POST",
                f"{LITELLM_BASE_URL}/v1/chat/completions",
                headers=forward_headers,
                content=body,
            ) as response:
                status_code = response.status_code

                if status_code == 429:
                    await websocket.send_text(
                        _error_frame(
                            "rate_limit_error",
                            "Rate limit exceeded. Retry after a moment.",
                        )
                    )
                    had_error = True
                    return status_code, had_error

                if status_code != 200:
                    await websocket.send_text(
                        _error_frame(
                            "model_error",
                            f"Upstream model returned status {status_code}.",
                            {"status_code": status_code},
                        )
                    )
                    had_error = True
                    return status_code, had_error

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data == "[DONE]":
                            await websocket.send_text(json.dumps({"type": "stream_complete"}))
                            break
                        await websocket.send_text(data)

    except httpx.HTTPError as exc:
        logger.warning("WS stream HTTP error request_id=%s: %s", request_id, exc)
        await websocket.send_text(
            _error_frame("stream_error", "Stream interrupted due to an upstream error.")
        )
        had_error = True

    return status_code, had_error


# ── Main WebSocket handler ────────────────────────────────────────────────────


async def ws_chat_completions(websocket: WebSocket) -> None:
    """WebSocket handler for /ws/v1/chat/completions.

    Lifecycle:
      1. Extract consumer identity from X-Consumer-Username (set by Kong key-auth).
      2. Enforce per-key connection limit; reject with 4029 if exceeded.
      3. Accept the connection and enter the receive loop.
      4. Each received JSON frame: validate → stream via LiteLLM SSE → forward deltas.
      5. On idle timeout: send connection_closing, close 1001.
      6. On WebSocketDisconnect: clean up and exit.
      7. Finally: always release the connection counter.
    """
    consumer_id: str = websocket.headers.get("x-consumer-username", "")
    connection_id: str = str(uuid.uuid4())
    app_state = websocket.app.state

    accepted = await acquire_connection(app_state, consumer_id)
    if not accepted:
        await websocket.close(
            code=4029,
            reason=_error_frame(
                "connection_limit_exceeded",
                "Maximum concurrent connections per API key reached.",
                {"limit": WS_MAX_CONNECTIONS_PER_KEY},
            ),
        )
        return

    await websocket.accept()
    logger.info(
        "WS connection opened consumer=%s connection_id=%s", consumer_id, connection_id
    )

    is_streaming: bool = False

    try:
        while True:
            try:
                raw_text: str = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=WS_IDLE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                await websocket.send_text(
                    _error_frame("connection_closing", "Idle timeout exceeded.")
                )
                await websocket.close(1001)
                break

            # Stream-in-progress guard (FR-013)
            if is_streaming:
                await websocket.send_text(
                    _error_frame(
                        "stream_in_progress",
                        "A stream is already active. Wait for stream_complete before sending a new request.",
                    )
                )
                continue

            # Parse payload
            try:
                payload: dict[str, Any] = json.loads(raw_text)
            except json.JSONDecodeError:
                await websocket.send_text(
                    _error_frame("validation_error", "Request body is not valid JSON.")
                )
                continue

            if not payload.get("model") or not payload.get("messages"):
                await websocket.send_text(
                    _error_frame(
                        "validation_error",
                        "Request must include non-empty 'model' and 'messages' fields.",
                    )
                )
                continue

            # Run validation gates (vision / FC / SO)
            raw_body = json.dumps(payload).encode()
            validated_body = await _run_validation_gates(raw_body, websocket)
            if validated_body is None:
                continue  # gate sent an error frame; loop to next message

            forward_headers: dict[str, str] = dict(websocket.headers)
            request_id = str(uuid.uuid4())
            model_name: str = payload.get("model", "")
            is_streaming = True

            # One OTel CHAIN span per stream request — no prompt content in attributes
            with tracer.start_as_current_span(
                "ws.stream_request",
                kind=SpanKind.INTERNAL,
            ) as span:
                span.set_attribute("ws.request_id", request_id)
                span.set_attribute("ws.connection_id", connection_id)
                span.set_attribute("ws.model", model_name)
                span.set_attribute("ws.consumer_id", consumer_id)

                status_code, had_error = await _stream_to_ws(
                    websocket, validated_body, forward_headers, request_id
                )

            _write_ws_audit(
                consumer_id=consumer_id,
                request_id=request_id,
                model_name=model_name,
                status_code=status_code,
                stream_error=had_error,
            )
            is_streaming = False

    except WebSocketDisconnect:
        logger.info(
            "WS connection closed consumer=%s connection_id=%s", consumer_id, connection_id
        )
    except Exception as exc:
        logger.exception(
            "Unexpected WS error consumer=%s connection_id=%s: %s",
            consumer_id, connection_id, exc,
        )
    finally:
        await release_connection(app_state, consumer_id)
        is_streaming = False
        logger.info(
            "WS connection released consumer=%s connection_id=%s", consumer_id, connection_id
        )
