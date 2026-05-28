"""
Contract tests for streaming POST /v1/chat/completions (stream: true).

US1: SSE response format, Content-Type, chunk JSON structure, DONE sentinel, TTFT, finish_reason.
US2: Non-streaming path unchanged after streaming enabled (regression guard, SC-003).
US3: Authentication enforced on streaming requests.
US4: Invalid inputs return 400 before any stream opens.
"""
import json
import time
from typing import Generator

import pytest
import requests


# ── Helpers ───────────────────────────────────────────────────────────────────


def _iter_sse_lines(response: requests.Response) -> Generator[str, None, None]:
    """Yield non-empty data lines from an SSE response."""
    for raw in response.iter_lines(decode_unicode=True):
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        line = raw.strip()
        if line.startswith("data:"):
            yield line[len("data:"):].strip()


def _collect_stream(
    kong_base_url: str,
    auth_headers: dict[str, str],
    payload: dict,
    timeout: int = 30,
) -> tuple[list[dict], bool]:
    """
    Send a streaming request and return (chunks, done_received).

    chunks: list of parsed JSON objects from data: lines (excluding [DONE])
    done_received: True if data: [DONE] was the last event
    """
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        headers={**auth_headers, "Content-Type": "application/json"},
        stream=True,
        timeout=timeout,
    )
    resp.raise_for_status()

    chunks: list[dict] = []
    done_received = False

    for data in _iter_sse_lines(resp):
        if data == "[DONE]":
            done_received = True
            break
        try:
            chunks.append(json.loads(data))
        except json.JSONDecodeError as exc:
            pytest.fail(f"SSE chunk is not independently parseable JSON: {data!r} — {exc}")

    return chunks, done_received


# ── US1: Receive a Streaming Chat Response ────────────────────────────────────


def test_streaming_response_status_and_content_type(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """HTTP 200, Content-Type: text/event-stream, required platform headers present."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Say one word."}],
            "stream": True,
        },
        headers={**auth_headers, "Content-Type": "application/json"},
        stream=True,
        timeout=30,
    )

    assert resp.status_code == 200, \
        f"Expected 200, got {resp.status_code}: {resp.text[:200]}"

    content_type = resp.headers.get("Content-Type", "")
    assert content_type.startswith("text/event-stream"), \
        f"Expected Content-Type: text/event-stream, got {content_type!r}"

    assert resp.headers.get("X-Platform") == "inference-platform", \
        "X-Platform header must be 'inference-platform'"
    assert resp.headers.get("X-API-Version") == "1", \
        "X-API-Version header must be '1'"
    assert "x-request-id" in resp.headers or "X-Request-ID" in resp.headers, \
        "X-Request-ID header must be present"

    resp.close()


def test_streaming_chunk_format(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Each SSE chunk is independently parseable JSON with correct object type and delta key."""
    chunks, _ = _collect_stream(
        kong_base_url,
        auth_headers,
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Count: 1, 2, 3."}],
            "stream": True,
        },
    )

    assert len(chunks) > 0, "At least one JSON chunk must be received before [DONE]"

    for i, chunk in enumerate(chunks):
        # Each chunk must be independently parseable (enforced by _collect_stream)
        assert chunk.get("object") == "chat.completion.chunk", \
            f"Chunk {i}: object must be 'chat.completion.chunk', got {chunk.get('object')!r}"
        assert isinstance(chunk.get("id"), str) and chunk["id"], \
            f"Chunk {i}: id must be a non-empty string"
        assert isinstance(chunk.get("model"), str) and chunk["model"], \
            f"Chunk {i}: model must be a non-empty string"

        choices = chunk.get("choices", [])
        assert len(choices) >= 1, f"Chunk {i}: choices must contain at least one element"
        assert "delta" in choices[0], \
            f"Chunk {i}: choices[0] must contain a 'delta' key"


def test_streaming_done_sentinel(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Stream ends with data: [DONE]; concatenated delta.content is non-empty."""
    chunks, done_received = _collect_stream(
        kong_base_url,
        auth_headers,
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Say hello."}],
            "stream": True,
        },
    )

    assert done_received, "Stream must terminate with data: [DONE]"

    assembled = "".join(
        c["choices"][0]["delta"].get("content", "")
        for c in chunks
        if c.get("choices") and c["choices"][0].get("delta")
    )
    assert len(assembled) > 0, \
        f"Concatenated delta.content values must form a non-empty string, got {assembled!r}"


def test_streaming_ttft(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    """First chunk arrives within 2 seconds of request send (SC-001)."""
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Hi."}],
        "stream": True,
    }

    t_start = time.monotonic()
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        headers={**auth_headers, "Content-Type": "application/json"},
        stream=True,
        timeout=30,
    )
    assert resp.status_code == 200

    first_chunk_data: str | None = None
    for data in _iter_sse_lines(resp):
        if data != "[DONE]":
            first_chunk_data = data
            break

    ttft = time.monotonic() - t_start
    resp.close()

    assert first_chunk_data is not None, "At least one data chunk must be received"
    assert ttft < 2.0, \
        f"Time to first token {ttft:.3f}s exceeds 2.0s limit (SC-001 p95 < 2s)"


def test_streaming_finish_reason(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Intermediate chunks have finish_reason null; final content chunk has a valid finish_reason."""
    chunks, done_received = _collect_stream(
        kong_base_url,
        auth_headers,
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "What is 1+1?"}],
            "stream": True,
        },
    )

    assert done_received, "Stream must end with [DONE]"
    assert len(chunks) > 0

    # All intermediate chunks must have finish_reason null
    intermediate = [c for c in chunks[:-1] if c.get("choices")]
    for i, chunk in enumerate(intermediate):
        fr = chunk["choices"][0].get("finish_reason")
        assert fr is None, \
            f"Intermediate chunk {i} must have finish_reason null, got {fr!r}"

    # Final chunk (before DONE) must have a valid finish_reason
    final_with_choice = [c for c in chunks if c.get("choices")]
    if final_with_choice:
        final_fr = final_with_choice[-1]["choices"][0].get("finish_reason")
        assert final_fr in {"stop", "length", "content_filter"}, \
            f"Final chunk finish_reason must be stop|length|content_filter, got {final_fr!r}"


# ── US2: Non-Streaming Requests Are Unaffected ───────────────────────────────


def test_non_streaming_unchanged(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """stream omitted → complete JSON response, Content-Type: application/json (SC-003)."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
        },
        headers={**auth_headers, "Content-Type": "application/json"},
        timeout=30,
    )

    assert resp.status_code == 200
    content_type = resp.headers.get("Content-Type", "")
    assert "application/json" in content_type, \
        f"Non-streaming must return application/json, got {content_type!r}"

    body = resp.json()
    content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    assert isinstance(content, str) and len(content) > 0, \
        "choices[0].message.content must be a non-empty string"

    usage = body.get("usage", {})
    assert isinstance(usage.get("prompt_tokens"), int) and usage["prompt_tokens"] > 0
    assert isinstance(usage.get("completion_tokens"), int) and usage["completion_tokens"] > 0


def test_stream_false_explicit(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """stream: false explicitly → complete JSON response, not SSE."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Say yes."}],
            "stream": False,
        },
        headers={**auth_headers, "Content-Type": "application/json"},
        timeout=30,
    )

    assert resp.status_code == 200
    content_type = resp.headers.get("Content-Type", "")
    assert "application/json" in content_type, \
        f"stream:false must return application/json, got {content_type!r}"
    assert "text/event-stream" not in content_type

    body = resp.json()
    assert body.get("object") == "chat.completion", \
        "stream:false response object must be 'chat.completion'"
    assert "choices" in body and len(body["choices"]) > 0
    assert body["choices"][0].get("message", {}).get("content")


# ── US3: Streaming Requests Respect Authentication ────────────────────────────


def test_streaming_unauthenticated_returns_401(kong_base_url: str) -> None:
    """Streaming request without Authorization header returns 401 at Kong; no SSE body."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "No auth."}],
            "stream": True,
        },
        headers={"Content-Type": "application/json"},
        timeout=10,
    )

    assert resp.status_code == 401, \
        f"Expected 401 for unauthenticated streaming request, got {resp.status_code}"

    content_type = resp.headers.get("Content-Type", "")
    assert "text/event-stream" not in content_type, \
        "No SSE response must be sent for unauthenticated requests"


def test_streaming_invalid_key_returns_401(kong_base_url: str) -> None:
    """Streaming request with invalid API key returns 401 at Kong."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Bad key."}],
            "stream": True,
        },
        headers={
            "Authorization": "Bearer totally-invalid-key-xyz",
            "Content-Type": "application/json",
        },
        timeout=10,
    )

    assert resp.status_code == 401, \
        f"Expected 401 for invalid key, got {resp.status_code}"


# ── US4: Invalid Inputs Return 400 on Streaming Requests ─────────────────────


def test_streaming_invalid_model_returns_400(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Unknown model + stream:true returns HTTP 400 in < 0.5s; no SSE body opened."""
    payload = {
        "model": "nonexistent-model-xyz",
        "messages": [{"role": "user", "content": "This must fail fast."}],
        "stream": True,
    }

    t_start = time.monotonic()
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        headers={**auth_headers, "Content-Type": "application/json"},
        timeout=10,
    )
    elapsed = time.monotonic() - t_start

    assert resp.status_code == 400, \
        f"Expected 400 for unknown model with stream:true, got {resp.status_code}: {resp.text}"
    assert elapsed < 0.5, \
        f"Streaming 400 took {elapsed:.3f}s — must return without upstream call (SC-004)"

    content_type = resp.headers.get("Content-Type", "")
    assert "text/event-stream" not in content_type, \
        "HTTP 400 must not open an SSE stream"

    body_text = resp.text
    assert "nonexistent-model-xyz" in body_text, \
        f"Error body must name the invalid model, got: {body_text[:300]}"


def test_streaming_empty_messages_returns_400(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """stream:true with empty messages array returns HTTP 400."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={"model": "gpt-4o-mini", "messages": [], "stream": True},
        headers={**auth_headers, "Content-Type": "application/json"},
        timeout=10,
    )

    assert resp.status_code == 400, \
        f"Expected 400 for empty messages with stream:true, got {resp.status_code}"
