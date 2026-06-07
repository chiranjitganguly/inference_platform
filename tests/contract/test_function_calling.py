"""
Contract tests for POST /v1/chat/completions — function calling support (feature 019).

Tests cover all acceptance criteria from specs/019-function-calling/plan.md:
  AC-1  Auto tool selection + matching message → tool_calls           (US1)
  AC-2  arguments is valid JSON-parseable string                       (US1)
  AC-3  Auto tool selection + non-matching message → plain text        (US1)
  AC-4  Non-function-capable model + tools → 400                       (US3 rejection)
  AC-5  stream:true + tools → 400                                      (streaming guard)
  AC-6  tool_choice names unknown function → 400                       (US2 rejection)
  AC-7  Missing name in tool definition → 400                          (US4 validation)
  AC-8  tool_choice:none → plain text, no tool_calls                   (US3 pass-through)
  AC-9  Text-only regression → HTTP 200                                (regression)

All validation rejections use the OpenAI error envelope per ADR-018.
"""
from __future__ import annotations

import json

import pytest
import requests

# ── Helpers ───────────────────────────────────────────────────────────────────

_WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}

_INVOICE_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_invoice",
        "description": "Extract structured fields from invoice text",
        "parameters": {
            "type": "object",
            "properties": {
                "vendor": {"type": "string"},
                "total": {"type": "number"},
            },
            "required": ["vendor", "total"],
        },
    },
}


def _assert_openai_error(body: dict, expected_code: str) -> None:
    """Assert the response body uses the OpenAI error envelope (ADR-018)."""
    error = body.get("error", {})
    assert isinstance(error, dict), (
        f"Expected 'error' to be a dict (OpenAI envelope), got {type(error).__name__}"
    )
    assert error.get("type") in ("invalid_request_error", "upstream_error"), (
        f"Unexpected error.type: '{error.get('type')}'"
    )
    assert error.get("code") == expected_code, (
        f"Expected error.code='{expected_code}', got '{error.get('code')}'"
    )
    assert isinstance(error.get("message"), str) and len(error["message"]) > 0, (
        "error.message must be a non-empty string"
    )


# ── AC-1: Auto tool selection → tool_calls ───────────────────────────────────


def test_auto_tool_selection_returns_tool_call(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-1: Matching message with auto tool_choice returns finish_reason=tool_calls."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "tool_choice": "auto",
            "tools": [_WEATHER_TOOL],
            "messages": [{"role": "user", "content": "What is the weather in London?"}],
        },
        headers=auth_headers,
        timeout=45,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls", (
        f"Expected finish_reason='tool_calls', got '{choice['finish_reason']}'"
    )
    tool_calls = choice["message"].get("tool_calls", [])
    assert len(tool_calls) >= 1, "Expected at least one tool call"
    assert tool_calls[0]["function"]["name"] == "get_weather", (
        f"Expected function name 'get_weather', got '{tool_calls[0]['function']['name']}'"
    )


# ── AC-2: arguments is valid JSON ────────────────────────────────────────────


def test_tool_arguments_is_valid_json(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-2: function.arguments in the response is always a valid JSON-parseable string."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "tool_choice": "auto",
            "tools": [_WEATHER_TOOL],
            "messages": [{"role": "user", "content": "What's the weather in Paris?"}],
        },
        headers=auth_headers,
        timeout=45,
    )
    assert resp.status_code == 200
    body = resp.json()
    tool_calls = body["choices"][0]["message"].get("tool_calls", [])
    if not tool_calls:
        pytest.skip("Model did not generate a tool call for this message")

    for call in tool_calls:
        args_str = call["function"]["arguments"]
        assert isinstance(args_str, str), "arguments must be a string"
        parsed = json.loads(args_str)  # raises if invalid JSON
        assert isinstance(parsed, dict), "arguments must parse to a JSON object"


# ── AC-3: Non-matching message → plain text ───────────────────────────────────


def test_auto_tool_non_matching_returns_text(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-3: Non-matching message returns plain text with finish_reason=stop."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "tool_choice": "auto",
            "tools": [_WEATHER_TOOL],
            "messages": [{"role": "user", "content": "What year did World War II end?"}],
        },
        headers=auth_headers,
        timeout=45,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    choice = body["choices"][0]
    assert choice["finish_reason"] == "stop", (
        f"Expected finish_reason='stop', got '{choice['finish_reason']}'"
    )
    assert not choice["message"].get("tool_calls"), (
        "Expected no tool_calls for non-matching message"
    )
    content = choice["message"].get("content", "")
    assert isinstance(content, str) and len(content) > 0, (
        "Expected non-empty text content for non-matching message"
    )


# ── AC-4: Non-FC model rejected ───────────────────────────────────────────────


def test_non_fc_model_rejected(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-4: Model without function-calling capability returns 400."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "claude-haiku",
            "tools": [_WEATHER_TOOL],
            "messages": [{"role": "user", "content": "test"}],
        },
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    body = resp.json()
    _assert_openai_error(body, "function_calling_model_required")
    assert "claude-haiku" in body["error"]["message"], (
        "Error message must name the rejected model 'claude-haiku'"
    )


# ── AC-5: Streaming + tools rejected ─────────────────────────────────────────


def test_streaming_with_tools_rejected(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-5: stream=true with non-empty tools returns 400."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "stream": True,
            "tools": [_WEATHER_TOOL],
            "messages": [{"role": "user", "content": "test"}],
        },
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    _assert_openai_error(resp.json(), "function_calling_streaming_not_supported")


# ── AC-6: Unknown tool_choice function ────────────────────────────────────────


def test_tool_choice_unknown_function_rejected(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-6: tool_choice names a function not in tools → 400."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "tool_choice": {"type": "function", "function": {"name": "nonexistent_fn"}},
            "tools": [_WEATHER_TOOL],
            "messages": [{"role": "user", "content": "test"}],
        },
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    body = resp.json()
    _assert_openai_error(body, "tool_choice_function_not_found")
    assert "nonexistent_fn" in body["error"]["message"], (
        "Error message must name the missing function"
    )


# ── AC-7: Missing name in tool definition ─────────────────────────────────────


def test_tool_missing_name_rejected(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-7: Tool definition missing 'name' field → 400 invalid_tool_definition."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "tools": [
                {
                    "type": "function",
                    "function": {
                        # no "name" field
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            "messages": [{"role": "user", "content": "test"}],
        },
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    _assert_openai_error(resp.json(), "invalid_tool_definition")


# ── AC-8: tool_choice:none → plain text ──────────────────────────────────────


def test_tool_choice_none_returns_text(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-8: tool_choice:none suppresses tool invocation even for matching message."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "tool_choice": "none",
            "tools": [_WEATHER_TOOL],
            "messages": [{"role": "user", "content": "What is the weather in Tokyo?"}],
        },
        headers=auth_headers,
        timeout=45,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    choice = resp.json()["choices"][0]
    assert choice["finish_reason"] == "stop", (
        f"Expected finish_reason='stop' with tool_choice:none, got '{choice['finish_reason']}'"
    )
    assert not choice["message"].get("tool_calls"), (
        "Expected no tool_calls when tool_choice:none"
    )


# ── AC-9: Text-only regression ───────────────────────────────────────────────


def test_text_only_regression(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-9: Text-only requests are unaffected by the function-calling gate."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "What is 2 + 2?"}],
        },
        headers=auth_headers,
        timeout=30,
    )
    assert resp.status_code == 200, (
        f"Text-only request must return 200 after function calling feature added. "
        f"Got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    content = body["choices"][0]["message"].get("content", "")
    assert isinstance(content, str) and len(content) > 0, (
        "Text-only response must have non-empty content"
    )
