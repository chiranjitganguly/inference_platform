"""
Guardrails service — multimodal vision validation + request proxy.

Responsibilities:
- Vision request validation: model capability check, image format/size/count enforcement
- 503 error body normalisation to the platform structured error schema
- Metadata-only audit logging (no prompt content — constitution §II)
- Embeddings no_log injection
"""
from __future__ import annotations

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
except ImportError:
    from vision import (  # flat import (Docker — CWD is /app)  # noqa: PLC0415
        VisionCapabilityCache,
        count_image_parts,
        has_image_parts,
        inject_detail_defaults,
        validate_vision_request,
    )

logger = logging.getLogger("guardrails")
audit = logging.getLogger("guardrails.audit")

LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    cache = VisionCapabilityCache()
    await cache.load()
    application.state.vision_cache = cache
    logger.info(
        "VisionCapabilityCache loaded: %d vision models", len(cache.vision_model_names)
    )
    yield


app = FastAPI(title="Guardrails Service", version="0.2.0", lifespan=_lifespan)


async def _stream_bytes(response: httpx.Response) -> AsyncIterator[bytes]:
    async for chunk in response.aiter_bytes():
        yield chunk


def _inject_no_log(body: bytes) -> bytes:
    """Inject no_log=True so LiteLLM skips Phoenix/Langfuse callbacks for embeddings."""
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
    body = await request.body()

    if path == "v1/embeddings" and request.method == "POST":
        body = _inject_no_log(body)

    image_part_count = 0

    if path == "v1/chat/completions" and request.method == "POST":
        result = await _validate_vision(body, request)
        if isinstance(result, Response):
            return result
        body, image_part_count = result

    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "transfer-encoding")
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        upstream = await client.request(
            method=request.method,
            url=f"{LITELLM_BASE_URL}/{path}",
            headers=headers,
            content=body,
            params=dict(request.query_params),
        )

    _write_audit(request, body, upstream.status_code, image_part_count)

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

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=dict(upstream.headers),
    )


async def _validate_vision(
    body: bytes, request: Request
) -> tuple[bytes, int] | Response:
    """
    Run vision validation when a chat completion request contains image parts.

    Returns (body, image_part_count) on success (body may be modified with detail defaults).
    Returns a Response on validation failure.
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


def _write_audit(
    request: Request, body: bytes, status_code: int, image_part_count: int = 0
) -> None:
    """Write a metadata-only audit entry — no prompt content (constitution §II)."""
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
    }
    audit.info(json.dumps(entry))


def _normalise_503(upstream: httpx.Response, request_body: bytes) -> Response:
    """Reformat LiteLLM's 503 body to the platform structured error schema."""
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
