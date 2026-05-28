"""
Contract tests for POST /v1/chat/completions.

US1: Basic chat completion response shape and required fields.
US2: Phoenix Arize span attributes (obs profile — skipped if Phoenix unreachable).
US3: Langfuse trace creation and prompt linking (obs profile — skipped if Langfuse unreachable).
US4: Invalid model name → 400; empty messages → 400; unauthenticated → 401.
"""
import time

import pytest
import requests


# ── US1: Submit a Chat Completion Request ─────────────────────────────────────


def test_basic_chat_completion(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    """HTTP 200, content/token counts/finish_reason present, required response headers set."""
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "What is 2 + 2?"}],
    }
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        headers=auth_headers,
        timeout=30,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    body = resp.json()
    choices = body.get("choices", [])
    assert len(choices) >= 1, "choices must contain at least one entry"

    message = choices[0].get("message", {})
    assert isinstance(message.get("content"), str) and len(message["content"]) > 0, \
        "choices[0].message.content must be a non-empty string"

    finish_reason = choices[0].get("finish_reason")
    assert finish_reason in {"stop", "length", "content_filter"}, \
        f"finish_reason must be stop|length|content_filter, got {finish_reason!r}"

    usage = body.get("usage", {})
    assert isinstance(usage.get("prompt_tokens"), int) and usage["prompt_tokens"] > 0, \
        "usage.prompt_tokens must be a positive integer"
    assert isinstance(usage.get("completion_tokens"), int) and usage["completion_tokens"] > 0, \
        "usage.completion_tokens must be a positive integer"

    # Required response headers (added by Kong plugins)
    assert "x-request-id" in resp.headers or "X-Request-ID" in resp.headers, \
        "X-Request-ID header must be present"
    assert resp.headers.get("X-Platform") == "inference-platform", \
        "X-Platform header must be 'inference-platform'"
    assert resp.headers.get("X-API-Version") == "1", \
        "X-API-Version header must be '1'"


def test_response_is_complete_json_not_stream(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Non-streaming request returns a complete JSON document (object, not chunked stream)."""
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Say hi."}],
        "stream": False,
    }
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        headers=auth_headers,
        timeout=30,
    )
    assert resp.status_code == 200
    assert resp.headers.get("Content-Type", "").startswith("application/json"), \
        "Non-streaming response must have Content-Type: application/json"
    body = resp.json()
    assert body.get("object") == "chat.completion", \
        "Response object field must be 'chat.completion'"


# ── US2: Traceability via Phoenix Arize ───────────────────────────────────────


def test_phoenix_span_attributes(
    kong_base_url: str,
    auth_headers: dict[str, str],
    phoenix_base_url: str,
) -> None:
    """Span in Phoenix carries llm.model_name, llm.token_count.prompt, llm.token_count.completion."""
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "What is the speed of light?"}],
        "metadata": {"team": "test-team", "request_id": "req-phoenix-001"},
    }
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        headers=auth_headers,
        timeout=30,
    )
    assert resp.status_code == 200
    usage = resp.json()["usage"]

    # Allow Phoenix a moment to ingest the span
    time.sleep(2)

    spans_resp = requests.get(f"{phoenix_base_url}/v1/spans?limit=1", timeout=10)
    assert spans_resp.status_code == 200, \
        f"Phoenix spans API returned {spans_resp.status_code}"

    spans = spans_resp.json()
    assert len(spans) > 0, "At least one span should exist in Phoenix after a completion"

    span = spans[0]
    attrs = span.get("attributes", {})
    assert attrs.get("llm.model_name") == "gpt-4o-mini", \
        f"llm.model_name mismatch: {attrs.get('llm.model_name')!r}"
    assert attrs.get("llm.token_count.prompt") == usage["prompt_tokens"], \
        "llm.token_count.prompt must match usage.prompt_tokens"
    assert attrs.get("llm.token_count.completion") == usage["completion_tokens"], \
        "llm.token_count.completion must match usage.completion_tokens"


def test_phoenix_metadata_tags(
    kong_base_url: str,
    auth_headers: dict[str, str],
    phoenix_base_url: str,
) -> None:
    """team and request_id metadata appear as tags on the Phoenix span."""
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Hello from metadata test."}],
        "metadata": {"team": "platform-team", "request_id": "req-meta-tag-001"},
    }
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        headers=auth_headers,
        timeout=30,
    )
    assert resp.status_code == 200

    time.sleep(2)

    spans_resp = requests.get(f"{phoenix_base_url}/v1/spans?limit=1", timeout=10)
    assert spans_resp.status_code == 200
    spans = spans_resp.json()
    assert len(spans) > 0

    attrs = spans[0].get("attributes", {})
    # LiteLLM arize_phoenix callback stores metadata fields under metadata.* keys
    team_val = attrs.get("metadata.team") or attrs.get("metadata", {}).get("team")
    req_val = attrs.get("metadata.request_id") or attrs.get("metadata", {}).get("request_id")
    assert team_val == "platform-team", \
        f"metadata.team not found or incorrect in span attributes: {attrs}"
    assert req_val == "req-meta-tag-001", \
        f"metadata.request_id not found or incorrect in span attributes: {attrs}"


# ── US3: Prompt-Linked Langfuse Trace ─────────────────────────────────────────


def test_langfuse_trace_created(
    kong_base_url: str,
    auth_headers: dict[str, str],
    langfuse_reachable: str,
    langfuse_auth_headers: dict[str, str],
) -> None:
    """Every completed request produces a Langfuse trace (unlinked when no prompt_name)."""
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Trace existence test."}],
        "metadata": {"team": "data-science", "request_id": "req-lf-trace-001"},
    }
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        headers=auth_headers,
        timeout=30,
    )
    assert resp.status_code == 200

    # Langfuse trace creation is async — allow processing time
    time.sleep(3)

    traces_resp = requests.get(
        f"{langfuse_reachable}/api/public/traces",
        headers=langfuse_auth_headers,
        params={"limit": 1},
        timeout=10,
    )
    assert traces_resp.status_code == 200, \
        f"Langfuse traces API returned {traces_resp.status_code}: {traces_resp.text}"
    data = traces_resp.json().get("data", [])
    assert len(data) > 0, "At least one Langfuse trace should exist after a completion"


def test_langfuse_metadata_tags(
    kong_base_url: str,
    auth_headers: dict[str, str],
    langfuse_reachable: str,
    langfuse_auth_headers: dict[str, str],
) -> None:
    """team and request_id appear in Langfuse trace metadata."""
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Langfuse metadata tag test."}],
        "metadata": {"team": "ml-team", "request_id": "req-lf-meta-001"},
    }
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        headers=auth_headers,
        timeout=30,
    )
    assert resp.status_code == 200

    time.sleep(3)

    traces_resp = requests.get(
        f"{langfuse_reachable}/api/public/traces",
        headers=langfuse_auth_headers,
        params={"limit": 1},
        timeout=10,
    )
    assert traces_resp.status_code == 200
    data = traces_resp.json().get("data", [])
    assert len(data) > 0

    trace = data[0]
    meta = trace.get("metadata") or {}
    assert meta.get("team") == "ml-team", \
        f"Langfuse trace metadata.team mismatch: {meta}"
    assert meta.get("request_id") == "req-lf-meta-001", \
        f"Langfuse trace metadata.request_id mismatch: {meta}"


def test_langfuse_prompt_linked_trace(
    kong_base_url: str,
    auth_headers: dict[str, str],
    langfuse_reachable: str,
    langfuse_auth_headers: dict[str, str],
) -> None:
    """When prompt_name is provided, trace is linked to the production prompt version.

    Prerequisite: a prompt named 'system-v1' with label 'production' must exist in Langfuse.
    Create it with: python scripts/prompt-register.py && python scripts/prompt-promote.py
    """
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Prompt link test."}],
        "metadata": {
            "team": "data-science",
            "request_id": "req-lf-prompt-001",
            "prompt_name": "system-v1",
        },
    }
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        headers=auth_headers,
        timeout=30,
    )
    assert resp.status_code == 200

    time.sleep(3)

    traces_resp = requests.get(
        f"{langfuse_reachable}/api/public/traces",
        headers=langfuse_auth_headers,
        params={"limit": 1},
        timeout=10,
    )
    assert traces_resp.status_code == 200
    data = traces_resp.json().get("data", [])
    assert len(data) > 0

    trace = data[0]
    meta = trace.get("metadata") or {}
    # LiteLLM langfuse callback stores prompt_name in trace metadata
    prompt_name = meta.get("prompt_name") or meta.get("promptName")
    assert prompt_name == "system-v1", \
        f"Langfuse trace should carry prompt_name='system-v1', got metadata: {meta}"


# ── US4: Reject Invalid Model Names ───────────────────────────────────────────


def test_invalid_model_returns_400(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Unknown model name returns HTTP 400 with error body naming the model; response is fast."""
    payload = {
        "model": "nonexistent-model-xyz",
        "messages": [{"role": "user", "content": "This should fail fast."}],
    }
    start = time.monotonic()
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        headers=auth_headers,
        timeout=10,
    )
    elapsed = time.monotonic() - start

    assert resp.status_code == 400, \
        f"Expected 400 for unknown model, got {resp.status_code}: {resp.text}"
    assert elapsed < 0.5, \
        f"Invalid model 400 took {elapsed:.3f}s — should return without upstream call"
    body = resp.json()
    assert "error" in body or "message" in body, \
        f"Response must contain error details: {body}"
    body_text = str(body)
    assert "nonexistent-model-xyz" in body_text, \
        f"Error body must name the invalid model, got: {body_text}"


def test_empty_messages_returns_400(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Empty messages array returns HTTP 400."""
    payload = {"model": "gpt-4o-mini", "messages": []}
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 400, \
        f"Expected 400 for empty messages, got {resp.status_code}: {resp.text}"


def test_unauthenticated_chat_returns_401(kong_base_url: str) -> None:
    """Request without Authorization header is rejected at Kong with HTTP 401."""
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "No auth header."}],
    }
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        timeout=10,
    )
    assert resp.status_code == 401, \
        f"Expected 401 for unauthenticated request, got {resp.status_code}"
