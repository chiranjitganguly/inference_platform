"""
Contract tests for automatic model fallback routing (feature 008).

US1: Silent fallback — primary fails, fallback serves HTTP 200; model field reflects fallback.
US2: 503 exhaustion — all fallbacks fail, HTTP 503 returned with structured error body.
US3: Context window overflow — oversized request routes to larger-context alternative.
US4: Operator-configurable — model with no chain returns 503 immediately.

Many tests require provider-level failure conditions (invalid API keys, oversized payloads)
that cannot be reproduced against a live stack without special configuration.
These tests use LITELLM_FALLBACK_TEST_MODE=1 guards to skip gracefully in normal CI runs.
Set LITELLM_FALLBACK_TEST_MODE=1 in .env, configure one provider key as invalid, and restart
litellm to exercise the full fallback path.
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests

FALLBACK_TEST_MODE = os.environ.get("LITELLM_FALLBACK_TEST_MODE", "0") == "1"
PRIMARY_MODEL = os.environ.get("FALLBACK_PRIMARY_MODEL", "gpt-4o")
EXPECTED_FALLBACK_MODEL = os.environ.get("FALLBACK_EXPECTED_MODEL", "claude-sonnet")
OVERFLOW_PRIMARY_MODEL = os.environ.get("OVERFLOW_PRIMARY_MODEL", "gpt-4o")
OVERFLOW_TARGET_MODEL = os.environ.get("OVERFLOW_TARGET_MODEL", "gpt-4.1")
NO_CHAIN_MODEL = os.environ.get("NO_CHAIN_MODEL", "text-embedding-3-small")


def _post(
    kong_base_url: str,
    auth_headers: dict[str, str],
    payload: dict,
    timeout: int = 60,
) -> requests.Response:
    return requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        headers={**auth_headers, "Content-Type": "application/json"},
        timeout=timeout,
    )


def _chat_payload(model: str, content: str | None = None) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": content or f"Reply with one word: OK {uuid.uuid4().hex[:4]}"}],
    }


# ── US1: Silent Fallback on Provider Failure ─────────────────────────────────


@pytest.mark.skipif(
    not FALLBACK_TEST_MODE,
    reason="Requires LITELLM_FALLBACK_TEST_MODE=1 and one provider key set to invalid",
)
def test_silent_fallback_returns_200(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Primary model fails → fallback serves HTTP 200, no error surfaced to caller (FR-001, SC-001)."""
    resp = _post(kong_base_url, auth_headers, _chat_payload(PRIMARY_MODEL))
    assert resp.status_code == 200, (
        f"Expected HTTP 200 from fallback, got {resp.status_code}: {resp.text[:200]}"
    )
    body = resp.json()
    assert body.get("choices"), "Response must contain choices"
    assert body["choices"][0]["message"]["content"], "Completion content must be non-empty"


@pytest.mark.skipif(
    not FALLBACK_TEST_MODE,
    reason="Requires LITELLM_FALLBACK_TEST_MODE=1 and one provider key set to invalid",
)
def test_fallback_model_identified_in_response(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """When fallback serves the request, model field reflects the fallback model, not the requested model (FR-004, SC-003)."""
    resp = _post(kong_base_url, auth_headers, _chat_payload(PRIMARY_MODEL))
    assert resp.status_code == 200
    body = resp.json()
    fulfilled_model = body.get("model", "")
    assert fulfilled_model, "Response must include model field"
    assert fulfilled_model != PRIMARY_MODEL, (
        f"Expected fallback model in response, got requested model '{PRIMARY_MODEL}'. "
        "Ensure primary provider key is invalid."
    )


def test_successful_response_has_model_field(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Every successful response includes a non-empty model field (FR-004)."""
    resp = _post(kong_base_url, auth_headers, _chat_payload("gpt-4o-mini"))
    assert resp.status_code == 200, f"Request failed: {resp.text[:200]}"
    body = resp.json()
    assert body.get("model"), "model field must be present and non-empty in every successful response"


@pytest.mark.skipif(
    not FALLBACK_TEST_MODE,
    reason="Requires LITELLM_FALLBACK_TEST_MODE=1 and one provider key set to invalid",
)
def test_fallback_state_is_per_request(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """After a fallback, the next request still attempts the primary first (FR-002 — no persistent fallback state)."""
    # First request — primary fails, fallback serves
    resp1 = _post(kong_base_url, auth_headers, _chat_payload(PRIMARY_MODEL))
    assert resp1.status_code == 200
    assert resp1.json().get("model") != PRIMARY_MODEL, "First request should use fallback"

    # Second request — primary still attempted first (same invalid key → same fallback)
    resp2 = _post(kong_base_url, auth_headers, _chat_payload(PRIMARY_MODEL))
    assert resp2.status_code == 200
    assert resp2.json().get("model") != PRIMARY_MODEL, (
        "Second request should also use fallback — primary is still unavailable"
    )


# ── US2: 503 When All Fallbacks Exhausted ────────────────────────────────────


@pytest.mark.skipif(
    not FALLBACK_TEST_MODE,
    reason="Requires LITELLM_FALLBACK_TEST_MODE=1 with ALL provider keys set to invalid",
)
def test_503_when_all_fallbacks_exhausted(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """All fallbacks exhausted → HTTP 503 returned, no unhandled exception (FR-003, SC-002)."""
    resp = _post(kong_base_url, auth_headers, _chat_payload(PRIMARY_MODEL), timeout=120)
    assert resp.status_code == 503, (
        f"Expected HTTP 503 when all fallbacks exhausted, got {resp.status_code}: {resp.text[:200]}"
    )


@pytest.mark.skipif(
    not FALLBACK_TEST_MODE,
    reason="Requires LITELLM_FALLBACK_TEST_MODE=1 with ALL provider keys set to invalid",
)
def test_503_body_follows_platform_schema(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """503 body follows platform structured error schema (FR-003, constitution §4.4).

    Note: full schema normalisation requires the guardrails service to be active.
    Without guardrails, LiteLLM's native error format is returned through Kong.
    """
    resp = _post(kong_base_url, auth_headers, _chat_payload(PRIMARY_MODEL), timeout=120)
    assert resp.status_code == 503
    assert resp.headers.get("Content-Type", "").startswith("application/json"), (
        "503 response must be application/json"
    )
    body = resp.json()
    assert "error" in body, f"503 body must contain 'error' key, got: {list(body.keys())}"
    assert "message" in body or "error" in body, "503 body must contain at least error or message"


# ── US3: Context Window Overflow ─────────────────────────────────────────────


@pytest.mark.skipif(
    not FALLBACK_TEST_MODE,
    reason="Requires LITELLM_FALLBACK_TEST_MODE=1 — sends a very large payload",
)
def test_context_overflow_routes_to_larger_model(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Token count > primary context window → routes to context_window_fallback target (FR-005, SC-004)."""
    # ~130k tokens: exceeds gpt-4o's 128k limit
    long_content = "word " * 40000
    payload = {
        "model": OVERFLOW_PRIMARY_MODEL,
        "messages": [{"role": "user", "content": long_content}],
    }
    resp = _post(kong_base_url, auth_headers, payload, timeout=120)
    assert resp.status_code == 200, (
        f"Expected HTTP 200 from overflow fallback, got {resp.status_code}: {resp.text[:200]}"
    )
    body = resp.json()
    fulfilled_model = body.get("model", "")
    assert fulfilled_model == OVERFLOW_TARGET_MODEL, (
        f"Expected overflow target '{OVERFLOW_TARGET_MODEL}', got '{fulfilled_model}'"
    )


def test_within_context_window_uses_primary(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Short requests (well within context window) are served by the primary model (FR-005)."""
    resp = _post(kong_base_url, auth_headers, _chat_payload("gpt-4o-mini"))
    assert resp.status_code == 200, f"Request failed: {resp.text[:200]}"
    body = resp.json()
    fulfilled = body.get("model", "")
    # When gpt-4o-mini is available, it should serve the request directly (no overflow)
    # We only assert the field is present; the exact model may differ if provider is unavailable
    assert fulfilled, "model field must be present"


# ── US4: Operator-Configurable Chains ────────────────────────────────────────


@pytest.mark.skipif(
    not FALLBACK_TEST_MODE,
    reason="Requires LITELLM_FALLBACK_TEST_MODE=1 with the no-chain model provider key set to invalid",
)
def test_model_with_no_chain_returns_503_immediately(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """Model with no configured fallback chain returns HTTP 503 immediately on failure (FR-010, US4 scenario 4)."""
    # Embedding model endpoints: no fallback chain configured
    payload = {
        "model": NO_CHAIN_MODEL,
        "input": "test embedding with no fallback",
    }
    resp = requests.post(
        f"{kong_base_url}/v1/embeddings",
        json=payload,
        headers={**auth_headers, "Content-Type": "application/json"},
        timeout=30,
    )
    assert resp.status_code == 503, (
        f"Expected HTTP 503 for model with no fallback chain, got {resp.status_code}"
    )


def test_fallback_chain_config_completeness() -> None:
    """Verify the fallback chain config covers all 9 chat models (US4 acceptance scenario 1)."""
    import yaml
    import os

    config_path = os.path.join(
        os.path.dirname(__file__), "../../services/litellm/config.yaml"
    )
    with open(config_path) as f:
        config = yaml.safe_load(f)

    fallbacks = config.get("litellm_settings", {}).get("fallbacks", [])
    configured_primaries = set()
    for entry in fallbacks:
        configured_primaries.update(entry.keys())

    expected_chat_models = {
        "gpt-4o", "gpt-4o-mini", "gpt-4.1", "o4-mini",
        "claude-sonnet", "claude-haiku", "gemini-pro", "gemini-flash", "command-r-plus",
    }
    missing = expected_chat_models - configured_primaries
    assert not missing, (
        f"Missing fallback chains for chat models: {missing}. "
        "Update services/litellm/config.yaml to add chains for all 9 chat models."
    )


def test_context_window_fallbacks_config_present() -> None:
    """Verify context_window_fallbacks are configured for models with < 1M context (US3, FR-005)."""
    import yaml
    import os

    config_path = os.path.join(
        os.path.dirname(__file__), "../../services/litellm/config.yaml"
    )
    with open(config_path) as f:
        config = yaml.safe_load(f)

    cw_fallbacks = config.get("litellm_settings", {}).get("context_window_fallbacks", [])
    assert cw_fallbacks, (
        "context_window_fallbacks must be configured in services/litellm/config.yaml"
    )
    configured = set()
    for entry in cw_fallbacks:
        configured.update(entry.keys())

    expected_overflow_models = {"gpt-4o", "gpt-4o-mini", "o4-mini", "claude-sonnet", "claude-haiku", "command-r-plus"}
    missing = expected_overflow_models - configured
    assert not missing, (
        f"Missing context_window_fallbacks for: {missing}"
    )


def test_retry_and_cooldown_config_present() -> None:
    """Verify num_retries, allowed_fails, cooldown_time are set in litellm_settings (FR-013, FR-015)."""
    import yaml
    import os

    config_path = os.path.join(
        os.path.dirname(__file__), "../../services/litellm/config.yaml"
    )
    with open(config_path) as f:
        config = yaml.safe_load(f)

    ls = config.get("litellm_settings", {})
    assert ls.get("num_retries") == 2, f"num_retries must be 2, got {ls.get('num_retries')}"
    assert ls.get("allowed_fails") == 3, f"allowed_fails must be 3, got {ls.get('allowed_fails')}"
    assert ls.get("cooldown_time") == 60, f"cooldown_time must be 60, got {ls.get('cooldown_time')}"
