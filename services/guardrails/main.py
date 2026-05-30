"""
Guardrails service — foundation stub for feature 008.

Current responsibility: normalise LiteLLM's 503 error body to the platform schema
when all fallback models are exhausted.

Future phases will extend this service with:
- Presidio PII detection and redaction (pre-inference)
- Prompt injection scanning (LLM Guard)
- Toxicity scanning (post-inference)
- OPA policy enforcement

Integration note: This service must be added to docker-compose.yml under the
'safety' profile with Kong's upstream updated to point to port 8088 instead of
LiteLLM's port 4000 directly. Until then, 503 normalisation is bypassed and
LiteLLM's native error format is returned to callers through Kong.

To activate: add to docker-compose.yml (safety profile) and update Kong upstream.
"""
from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

logger = logging.getLogger("guardrails")

LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")

app = FastAPI(title="Guardrails Service", version="0.1.0")


async def _stream_bytes(response: httpx.Response) -> AsyncIterator[bytes]:
    async for chunk in response.aiter_bytes():
        yield chunk


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
def _inject_no_log(body: bytes) -> bytes:
    """Inject no_log=True so LiteLLM skips Phoenix/Langfuse callbacks for embeddings.

    Safe to call when callbacks are disabled — the metadata field is ignored by LiteLLM
    in that state, so this function is a no-op from LiteLLM's perspective.
    """
    try:
        payload: dict = json.loads(body)
    except Exception:
        return body
    payload.setdefault("metadata", {})["no_log"] = "True"
    return json.dumps(payload).encode()


async def proxy(request: Request, path: str) -> Response:
    body = await request.body()

    if path == "v1/embeddings" and request.method == "POST":
        body = _inject_no_log(body)

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
        req: dict = json.loads(request_body or b"{}")
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
