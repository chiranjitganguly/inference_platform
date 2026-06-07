"""
Contract tests for /ws/v1/chat/completions WebSocket streaming endpoint.

These tests verify the wire protocol defined in:
  specs/021-websocket-streaming/contracts/websocket-protocol.md

Tests run against a live stack (core profile).  They are skipped automatically
when the gateway is unreachable so CI does not fail in environments that only
run unit tests.

Run with:
  pytest tests/contract/test_ws_streaming.py -v

Prerequisites:
  make up-core && make seed-kong
  export SMOKE_API_KEY=<your-key>
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any  # noqa: F401 (re-exported for type annotation below)

import pytest

# websockets is installed transitively via uvicorn[standard]
try:
    from websockets.asyncio.client import connect as ws_connect
    from websockets.exceptions import InvalidStatus
except ImportError:
    pytest.skip("websockets package not available", allow_module_level=True)

# ── Configuration ─────────────────────────────────────────────────────────────

GATEWAY_HOST: str = os.environ.get("GATEWAY_HOST", "localhost")
GATEWAY_PORT: int = int(os.environ.get("GATEWAY_PORT", "8080"))
SMOKE_API_KEY: str = os.environ.get("SMOKE_API_KEY", "")
WS_URL: str = f"ws://{GATEWAY_HOST}:{GATEWAY_PORT}/ws/v1/chat/completions"

SKIP_IF_NO_KEY = pytest.mark.skipif(
    not SMOKE_API_KEY,
    reason="SMOKE_API_KEY not set — skipping live WebSocket tests",
)

SHORT_PAYLOAD: str = json.dumps(
    {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Say OK in one word."}],
    }
)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def collect_stream(ws: Any, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Collect all frames until stream_complete or timeout."""
    frames: list[dict] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            frame = json.loads(raw)
            frames.append(frame)
            if frame.get("type") == "stream_complete":
                break
            if frame.get("type", "").endswith("_error"):
                break
        except asyncio.TimeoutError:
            break
    return frames


# ── Tests ─────────────────────────────────────────────────────────────────────


@SKIP_IF_NO_KEY
@pytest.mark.asyncio
async def test_connect_unauthenticated() -> None:
    """Connecting without an Authorization header must be rejected (HTTP 401)."""
    with pytest.raises(InvalidStatus) as exc_info:
        async with ws_connect(WS_URL):
            pass
    assert exc_info.value.response.status_code == 401


@SKIP_IF_NO_KEY
@pytest.mark.asyncio
async def test_single_stream() -> None:
    """Connect, send one payload, verify token deltas arrive and stream_complete is last."""
    async with ws_connect(WS_URL, additional_headers={"Authorization": SMOKE_API_KEY}) as ws:
        await ws.send(SHORT_PAYLOAD)
        frames = await collect_stream(ws)

    assert frames, "No frames received"

    delta_frames = [f for f in frames if f.get("object") == "chat.completion.chunk"]
    assert delta_frames, "No chat.completion.chunk frames received"

    # At least one final chunk must have finish_reason set
    finish_reasons = [
        c.get("finish_reason")
        for f in delta_frames
        for c in f.get("choices", [])
        if c.get("finish_reason") is not None
    ]
    assert finish_reasons, "No frame with finish_reason set"

    # The very last frame must be stream_complete
    assert frames[-1].get("type") == "stream_complete", (
        f"Expected stream_complete as last frame, got: {frames[-1]}"
    )


@SKIP_IF_NO_KEY
@pytest.mark.asyncio
async def test_persistent_connection() -> None:
    """Two sequential requests on one connection — no reconnect between them."""
    async with ws_connect(WS_URL, additional_headers={"Authorization": SMOKE_API_KEY}) as ws:
        # First request
        await ws.send(SHORT_PAYLOAD)
        frames_1 = await collect_stream(ws)
        assert frames_1[-1].get("type") == "stream_complete", "First stream did not complete"

        # Second request on same connection
        await ws.send(SHORT_PAYLOAD)
        frames_2 = await collect_stream(ws)
        assert frames_2[-1].get("type") == "stream_complete", "Second stream did not complete"

    assert frames_1, "First stream delivered no frames"
    assert frames_2, "Second stream delivered no frames"


@SKIP_IF_NO_KEY
@pytest.mark.asyncio
async def test_stream_in_progress() -> None:
    """Sending a second payload while a stream is active must return stream_in_progress."""
    long_payload = json.dumps(
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Count slowly from 1 to 20, one number per line."}],
        }
    )

    async with ws_connect(WS_URL, additional_headers={"Authorization": SMOKE_API_KEY}) as ws:
        # Start a long stream
        await ws.send(long_payload)

        # Immediately send a second request before the first completes
        await ws.send(SHORT_PAYLOAD)

        # Collect frames until stream_complete
        all_frames: list[dict] = []
        saw_stream_in_progress = False
        saw_stream_complete = False

        for _ in range(200):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                frame = json.loads(raw)
                all_frames.append(frame)
                if frame.get("type") == "stream_in_progress":
                    saw_stream_in_progress = True
                if frame.get("type") == "stream_complete":
                    saw_stream_complete = True
                    break
            except asyncio.TimeoutError:
                break

    assert saw_stream_in_progress, "Expected stream_in_progress error frame, got: " + str(all_frames[:5])
    assert saw_stream_complete, "First stream did not complete after stream_in_progress error"


@SKIP_IF_NO_KEY
@pytest.mark.asyncio
async def test_invalid_payload() -> None:
    """Malformed JSON must return validation_error; connection must stay open for next request."""
    async with ws_connect(WS_URL, additional_headers={"Authorization": SMOKE_API_KEY}) as ws:
        # Send invalid JSON
        await ws.send("not valid json{{{{")
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        error_frame = json.loads(raw)
        assert error_frame.get("type") == "validation_error", f"Expected validation_error, got: {error_frame}"

        # Connection must still be open — send a valid request
        await ws.send(SHORT_PAYLOAD)
        frames = await collect_stream(ws)
        assert frames[-1].get("type") == "stream_complete", (
            "Expected stream_complete after recovery from validation_error"
        )


@SKIP_IF_NO_KEY
@pytest.mark.asyncio
async def test_missing_required_fields() -> None:
    """Payload missing model or messages must return validation_error without closing the connection."""
    async with ws_connect(WS_URL, additional_headers={"Authorization": SMOKE_API_KEY}) as ws:
        # Missing 'messages'
        await ws.send(json.dumps({"model": "gpt-4o-mini"}))
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        frame = json.loads(raw)
        assert frame.get("type") == "validation_error"

        # Connection stays open
        await ws.send(SHORT_PAYLOAD)
        frames = await collect_stream(ws)
        assert frames[-1].get("type") == "stream_complete"


@SKIP_IF_NO_KEY
@pytest.mark.asyncio
async def test_model_error() -> None:
    """Requesting a non-existent model must return a model_error or validation_error frame."""
    bad_payload = json.dumps(
        {
            "model": "non-existent-model-xyz-12345",
            "messages": [{"role": "user", "content": "Hello."}],
        }
    )
    async with ws_connect(WS_URL, additional_headers={"Authorization": SMOKE_API_KEY}) as ws:
        await ws.send(bad_payload)
        raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
        frame = json.loads(raw)
        assert frame.get("type") in ("model_error", "validation_error", "stream_error"), (
            f"Expected an error frame, got: {frame}"
        )

        # Connection stays open
        await ws.send(SHORT_PAYLOAD)
        frames = await collect_stream(ws)
        assert frames[-1].get("type") == "stream_complete"


