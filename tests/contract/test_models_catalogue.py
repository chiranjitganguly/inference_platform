"""Contract tests for GET /v1/models — model catalogue endpoint.

Run with:
    KONG_BASE_URL=http://localhost:8080 SMOKE_API_KEY=<key> pytest tests/contract/test_models_catalogue.py -v
"""

from __future__ import annotations

import requests
import pytest

EXPECTED_MODELS: set[str] = {
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "o4-mini",
    "claude-sonnet",
    "claude-haiku",
    "gemini-pro",
    "gemini-flash",
    "command-r-plus",
    "text-embedding-3-small",
    "text-embedding-3-large",
}

EXPECTED_PROVIDERS: set[str] = {"openai", "anthropic", "google", "cohere"}
VALID_TIERS: set[str] = {"standard", "premium"}
VALID_TYPES: set[str] = {"chat", "embedding"}
VALID_STATUSES: set[str] = {"available", "unavailable"}
VALID_CAPABILITIES: set[str] = {"chat", "streaming", "function-calling", "vision", "embeddings"}


# ── US1: Browse Full Model Catalogue ─────────────────────────────────────────


def test_authenticated_request_returns_200(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


def test_response_content_type_is_json(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    assert "application/json" in resp.headers.get("Content-Type", "")


def test_envelope_has_list_object(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    body = resp.json()
    assert body.get("object") == "list", f"Expected object=='list', got: {body.get('object')}"
    assert isinstance(body.get("data"), list), "Expected 'data' to be a list"


def test_catalogue_contains_exactly_eleven_models(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    data = resp.json()["data"]
    assert len(data) == 11, f"Expected 11 models, got {len(data)}"


def test_all_four_providers_present(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    data = resp.json()["data"]
    providers = {entry["model_info"]["provider"] for entry in data}
    assert providers == EXPECTED_PROVIDERS, f"Provider mismatch: {providers}"


def test_every_entry_has_required_fields(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    data = resp.json()["data"]
    for entry in data:
        model_id = entry.get("id", "<unknown>")
        assert "id" in entry, f"Missing 'id' in entry"
        assert "model_info" in entry, f"Missing 'model_info' in {model_id}"
        info = entry["model_info"]
        for field in ("provider", "tier", "type", "status", "context_window", "capabilities"):
            assert field in info, f"Missing '{field}' in model_info for {model_id}"


def test_tier_values_are_valid(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    data = resp.json()["data"]
    for entry in data:
        tier = entry["model_info"]["tier"]
        assert tier in VALID_TIERS, f"{entry['id']}: invalid tier '{tier}'"


def test_type_values_are_valid(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    data = resp.json()["data"]
    for entry in data:
        model_type = entry["model_info"]["type"]
        assert model_type in VALID_TYPES, f"{entry['id']}: invalid type '{model_type}'"


def test_status_values_are_valid(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    data = resp.json()["data"]
    for entry in data:
        status = entry["model_info"]["status"]
        assert status in VALID_STATUSES, f"{entry['id']}: invalid status '{status}'"


def test_context_window_is_positive_integer(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    data = resp.json()["data"]
    for entry in data:
        cw = entry["model_info"]["context_window"]
        assert isinstance(cw, int) and cw > 0, f"{entry['id']}: invalid context_window '{cw}'"


def test_capabilities_uses_controlled_vocabulary(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    data = resp.json()["data"]
    for entry in data:
        caps = set(entry["model_info"]["capabilities"])
        invalid = caps - VALID_CAPABILITIES
        assert not invalid, f"{entry['id']}: unknown capabilities {invalid}"


def test_response_includes_platform_headers(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    assert resp.headers.get("X-Platform") == "inference-platform"
    assert resp.headers.get("X-API-Version") == "1"
    assert "X-Request-ID" in resp.headers


# ── US2: Distinguish Chat vs Embedding Models ─────────────────────────────────


def test_exactly_nine_chat_models(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    data = resp.json()["data"]
    chat_models = [e for e in data if e["model_info"]["type"] == "chat"]
    assert len(chat_models) == 9, f"Expected 9 chat models, got {len(chat_models)}"


def test_exactly_two_embedding_models(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    data = resp.json()["data"]
    embedding_models = [e for e in data if e["model_info"]["type"] == "embedding"]
    assert len(embedding_models) == 2, f"Expected 2 embedding models, got {len(embedding_models)}"


def test_embedding_models_have_dimensions(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    data = resp.json()["data"]
    for entry in data:
        info = entry["model_info"]
        if info["type"] == "embedding":
            assert "dimensions" in info, f"{entry['id']}: embedding model missing 'dimensions'"
            assert isinstance(info["dimensions"], int) and info["dimensions"] > 0, (
                f"{entry['id']}: 'dimensions' must be a positive integer"
            )


def test_chat_models_have_no_dimensions(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    data = resp.json()["data"]
    for entry in data:
        info = entry["model_info"]
        if info["type"] == "chat":
            assert "dimensions" not in info, (
                f"{entry['id']}: chat model must not have 'dimensions'"
            )


def test_embedding_models_have_embeddings_capability(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    data = resp.json()["data"]
    for entry in data:
        info = entry["model_info"]
        if info["type"] == "embedding":
            assert "embeddings" in info["capabilities"], (
                f"{entry['id']}: embedding model must list 'embeddings' capability"
            )


def test_chat_models_do_not_have_embeddings_capability(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    data = resp.json()["data"]
    for entry in data:
        info = entry["model_info"]
        if info["type"] == "chat":
            assert "embeddings" not in info["capabilities"], (
                f"{entry['id']}: chat model must not list 'embeddings' capability"
            )


def test_embedding_model_dimensions_values(kong_base_url: str, auth_headers: dict[str, str]) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", headers=auth_headers, timeout=10)
    data = resp.json()["data"]
    embedding_by_id = {
        e["id"]: e["model_info"]["dimensions"]
        for e in data
        if e["model_info"]["type"] == "embedding"
    }
    assert embedding_by_id.get("text-embedding-3-small") == 1536
    assert embedding_by_id.get("text-embedding-3-large") == 3072


# ── US3: Reject Unauthenticated Requests ─────────────────────────────────────


def test_no_auth_header_returns_401(kong_base_url: str) -> None:
    resp = requests.get(f"{kong_base_url}/v1/models", timeout=10)
    assert resp.status_code == 401, f"Expected 401 without auth, got {resp.status_code}"


def test_invalid_api_key_returns_401(kong_base_url: str) -> None:
    resp = requests.get(
        f"{kong_base_url}/v1/models",
        headers={"Authorization": "Bearer invalid-key-that-does-not-exist"},
        timeout=10,
    )
    assert resp.status_code == 401, f"Expected 401 with invalid key, got {resp.status_code}"


def test_wrong_auth_scheme_returns_401(kong_base_url: str) -> None:
    resp = requests.get(
        f"{kong_base_url}/v1/models",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
        timeout=10,
    )
    assert resp.status_code == 401, f"Expected 401 with Basic auth, got {resp.status_code}"
