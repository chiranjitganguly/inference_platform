"""Contract tests for portal-backend GET /v1/spend endpoint.

Tests mock LiteLLM's /global/spend/keys and /global/spend/models responses
so the aggregation logic can be verified without a running LiteLLM instance.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app  # type: ignore[import]  # conftest.py adds portal-backend to sys.path

MASTER_KEY = "sk-master-test"

MOCK_KEYS_RESPONSE = [
    {
        "token": "abc123",
        "key_alias": "team-alpha",
        "spend": 9.341,
        "max_budget": 20.0,
        "budget_duration": "monthly",
        "budget_reset_at": "2026-06-01T00:00:00Z",
    },
    {
        "token": "def456",
        "key_alias": "team-beta",
        "spend": 3.506,
        "max_budget": None,
        "budget_duration": None,
        "budget_reset_at": None,
    },
]

MOCK_MODELS_RESPONSE = [
    {
        "model": "gpt-4o-mini",
        "spend": 8.12,
        "prompt_tokens": 1820000,
        "completion_tokens": 245000,
    },
    {
        "model": "claude-haiku",
        "spend": 4.727,
        "prompt_tokens": 980000,
        "completion_tokens": 118000,
    },
]


def _make_litellm_mock(keys_status: int = 200, models_status: int = 200) -> AsyncMock:
    """Return an AsyncMock for httpx.AsyncClient that returns preset responses."""
    mock_client = AsyncMock()

    async def _get(url: str, **kwargs: object) -> httpx.Response:
        if "spend/keys" in url:
            return httpx.Response(
                keys_status,
                json=MOCK_KEYS_RESPONSE if keys_status == 200 else {"error": "unauthorized"},
            )
        if "spend/models" in url:
            return httpx.Response(
                models_status,
                json=MOCK_MODELS_RESPONSE if models_status == 200 else {"error": "unauthorized"},
            )
        return httpx.Response(404, json={"error": "not_found"})

    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = _get
    return mock_client


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


# ── Happy path ────────────────────────────────────────────────────────────────


def test_spend_happy_path(client: TestClient) -> None:
    with patch("main.httpx.AsyncClient", return_value=_make_litellm_mock()):
        resp = client.get("/v1/spend", headers={"Authorization": f"Bearer {MASTER_KEY}"})

    assert resp.status_code == 200
    data = resp.json()
    assert "total_spend_usd" in data
    assert isinstance(data["by_model"], list)
    assert isinstance(data["by_key"], list)
    assert len(data["by_model"]) == 2
    assert len(data["by_key"]) == 2


# ── total_spend_usd equals sum of by_model spend ─────────────────────────────


def test_total_spend_equals_model_sum(client: TestClient) -> None:
    with patch("main.httpx.AsyncClient", return_value=_make_litellm_mock()):
        resp = client.get("/v1/spend", headers={"Authorization": f"Bearer {MASTER_KEY}"})

    data = resp.json()
    expected = sum(m["spend_usd"] for m in data["by_model"])
    assert abs(data["total_spend_usd"] - expected) < 1e-9


# ── budget_remaining_usd is null when max_budget is null ─────────────────────


def test_budget_remaining_null_when_no_ceiling(client: TestClient) -> None:
    with patch("main.httpx.AsyncClient", return_value=_make_litellm_mock()):
        resp = client.get("/v1/spend", headers={"Authorization": f"Bearer {MASTER_KEY}"})

    data = resp.json()
    beta = next(k for k in data["by_key"] if k["key_alias"] == "team-beta")
    assert beta["max_budget_usd"] is None
    assert beta["budget_remaining_usd"] is None


# ── key_id filter returns single matching key ─────────────────────────────────


def test_key_id_filter(client: TestClient) -> None:
    with patch("main.httpx.AsyncClient", return_value=_make_litellm_mock()):
        resp = client.get(
            "/v1/spend",
            params={"key_id": "team-alpha"},
            headers={"Authorization": f"Bearer {MASTER_KEY}"},
        )

    data = resp.json()
    assert len(data["by_key"]) == 1
    assert data["by_key"][0]["key_alias"] == "team-alpha"


# ── LiteLLM returns 401 → portal returns 401 ─────────────────────────────────


def test_litellm_401_returns_portal_401(client: TestClient) -> None:
    with patch(
        "main.httpx.AsyncClient",
        return_value=_make_litellm_mock(keys_status=401, models_status=401),
    ):
        resp = client.get("/v1/spend", headers={"Authorization": "Bearer bad-key"})

    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"


# ── LiteLLM returns 503 → portal returns 503 spend_store_unavailable ─────────


def test_litellm_503_returns_spend_store_unavailable(client: TestClient) -> None:
    with patch(
        "main.httpx.AsyncClient",
        return_value=_make_litellm_mock(keys_status=503, models_status=503),
    ):
        resp = client.get("/v1/spend", headers={"Authorization": f"Bearer {MASTER_KEY}"})

    assert resp.status_code == 503
    assert resp.json()["error"] == "spend_store_unavailable"
