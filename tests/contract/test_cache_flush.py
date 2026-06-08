"""
Contract tests for DELETE /cache/flush cache management endpoint.

Tests run against the FastAPI app directly (no live stack required) using
httpx.AsyncClient with mocked Redis and LiteLLM calls.

Run with:
  pytest tests/contract/test_cache_flush.py -v
"""
from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Import the app and helpers so we can patch at the right module path
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/portal-backend"))

from main import app  # noqa: E402


MASTER_KEY = "sk-master-test-key"
CONSUMER_KEY = "sk-consumer-key"

# Patch target for the module-level constant
MASTER_KEY_PATH = "main.LITELLM_MASTER_KEY"
SCAN_DEL_PATH = "main._redis_scan_del"
AUDIT_PATH = "main._write_cache_audit"
MODEL_NAMES_PATH = "main._get_model_names"

VALID_MODELS = ["gpt-4o", "gpt-4o-mini", "claude-sonnet", "claude-haiku"]


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ── Full flush (US1) ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_flush_success(client: AsyncClient) -> None:
    """Master key flushes all entries and returns keys_deleted count."""
    with (
        patch(MASTER_KEY_PATH, MASTER_KEY),
        patch(SCAN_DEL_PATH, new=AsyncMock(return_value=42)),
        patch(AUDIT_PATH) as mock_audit,
    ):
        resp = await client.delete("/cache/flush", headers={"Authorization": f"Bearer {MASTER_KEY}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["keys_deleted"] == 42
    assert "model" not in body or body["model"] is None
    mock_audit.assert_called_once()
    call_kwargs = mock_audit.call_args.kwargs
    assert call_kwargs["scope"] == "all"
    assert call_kwargs["model"] == "all"
    assert call_kwargs["keys_deleted"] == 42


@pytest.mark.asyncio
async def test_full_flush_no_key_returns_401(client: AsyncClient) -> None:
    """Missing Authorization header returns 401."""
    with patch(MASTER_KEY_PATH, MASTER_KEY):
        resp = await client.delete("/cache/flush")

    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"


@pytest.mark.asyncio
async def test_full_flush_non_master_returns_403(client: AsyncClient) -> None:
    """Non-master consumer key returns 403 before any Redis operation."""
    with (
        patch(MASTER_KEY_PATH, MASTER_KEY),
        patch(SCAN_DEL_PATH, new=AsyncMock(return_value=0)) as mock_redis,
    ):
        resp = await client.delete(
            "/cache/flush", headers={"Authorization": f"Bearer {CONSUMER_KEY}"}
        )

    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"
    mock_redis.assert_not_called()


@pytest.mark.asyncio
async def test_full_flush_empty_cache_returns_zero(client: AsyncClient) -> None:
    """Flushing an empty cache returns 200 with keys_deleted=0 (idempotent)."""
    with (
        patch(MASTER_KEY_PATH, MASTER_KEY),
        patch(SCAN_DEL_PATH, new=AsyncMock(return_value=0)),
        patch(AUDIT_PATH),
    ):
        resp = await client.delete("/cache/flush", headers={"Authorization": f"Bearer {MASTER_KEY}"})

    assert resp.status_code == 200
    assert resp.json()["keys_deleted"] == 0


# ── Model-scoped flush (US2) ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scoped_flush_success(client: AsyncClient) -> None:
    """Master key with valid model name removes only that model's entries."""
    with (
        patch(MASTER_KEY_PATH, MASTER_KEY),
        patch(MODEL_NAMES_PATH, new=AsyncMock(return_value=VALID_MODELS)),
        patch(SCAN_DEL_PATH, new=AsyncMock(return_value=15)) as mock_redis,
        patch(AUDIT_PATH) as mock_audit,
    ):
        resp = await client.delete(
            "/cache/flush?model=gpt-4o",
            headers={"Authorization": f"Bearer {MASTER_KEY}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["keys_deleted"] == 15
    assert body["model"] == "gpt-4o"

    # Verify SCAN pattern is model-scoped
    mock_redis.assert_called_once_with("llm_cache:gpt-4o:*")

    call_kwargs = mock_audit.call_args.kwargs
    assert call_kwargs["scope"] == "model"
    assert call_kwargs["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_scoped_flush_invalid_model_returns_422_with_valid_list(client: AsyncClient) -> None:
    """Unknown model name returns 422 with valid_models list; no Redis call made."""
    with (
        patch(MASTER_KEY_PATH, MASTER_KEY),
        patch(MODEL_NAMES_PATH, new=AsyncMock(return_value=VALID_MODELS)),
        patch(SCAN_DEL_PATH, new=AsyncMock(return_value=0)) as mock_redis,
    ):
        resp = await client.delete(
            "/cache/flush?model=nonexistent-model-xyz",
            headers={"Authorization": f"Bearer {MASTER_KEY}"},
        )

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "invalid_model"
    assert "valid_models" in body["detail"]
    assert set(body["detail"]["valid_models"]) == set(VALID_MODELS)
    mock_redis.assert_not_called()


@pytest.mark.asyncio
async def test_scoped_flush_non_master_returns_403(client: AsyncClient) -> None:
    """Non-master key on scoped flush returns 403 before any model validation or Redis call."""
    with (
        patch(MASTER_KEY_PATH, MASTER_KEY),
        patch(MODEL_NAMES_PATH, new=AsyncMock(return_value=VALID_MODELS)) as mock_models,
        patch(SCAN_DEL_PATH, new=AsyncMock(return_value=0)) as mock_redis,
    ):
        resp = await client.delete(
            "/cache/flush?model=gpt-4o",
            headers={"Authorization": f"Bearer {CONSUMER_KEY}"},
        )

    assert resp.status_code == 403
    mock_models.assert_not_called()
    mock_redis.assert_not_called()
