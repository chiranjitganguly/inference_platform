"""portal-backend — spend aggregation + cache management for the AI Inference Platform."""
from __future__ import annotations

import calendar
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from redis.exceptions import RedisError

app = FastAPI(title="portal-backend", version="1.0.0")

LITELLM_BASE_URL: str = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
REDIS_URL: str = os.environ.get("REDIS_URL", "redis://redis:6379")
LITELLM_MASTER_KEY: str = os.environ.get("LITELLM_MASTER_KEY", "")

audit = logging.getLogger("portal-backend.audit")


# ── Response models ───────────────────────────────────────────────────────────


class ModelSpend(BaseModel):
    model: str
    spend_usd: float
    prompt_tokens: int
    completion_tokens: int


class KeySpend(BaseModel):
    key_alias: str | None
    key_hash: str
    spend_usd: float
    max_budget_usd: float | None
    budget_remaining_usd: float | None
    budget_reset_at: str | None


class SpendReport(BaseModel):
    period_start: str
    period_end: str
    total_spend_usd: float
    by_model: list[ModelSpend]
    by_key: list[KeySpend]


class CacheFlushResult(BaseModel):
    keys_deleted: int
    model: str | None = None


# ── Shared helpers ────────────────────────────────────────────────────────────


def _current_period() -> tuple[str, str]:
    now = datetime.now(tz=timezone.utc)
    last_day = calendar.monthrange(now.year, now.month)[1]
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    period_end = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=0)
    return period_start.isoformat().replace("+00:00", "Z"), period_end.isoformat().replace("+00:00", "Z")


def _mask_key(token: str) -> str:
    suffix = token[-4:] if len(token) >= 4 else token
    return f"sk-...{suffix}"


def _build_error(error: str, message: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": error, "message": message, "detail": detail or {}}


# ── Cache management helpers ──────────────────────────────────────────────────


def _validate_master_key(authorization: str) -> None:
    """Raise HTTP 401 if key is absent, HTTP 403 if present but not the master key."""
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail=_build_error("unauthorized", "Master key required."),
        )
    raw_key = authorization.removeprefix("Bearer ").strip()
    if not raw_key or raw_key != LITELLM_MASTER_KEY:
        raise HTTPException(
            status_code=403,
            detail=_build_error("forbidden", "Only the platform master key may flush the cache."),
        )


async def _redis_scan_del(pattern: str) -> int:
    """Delete all Redis keys matching pattern using cursor-based SCAN. Returns count deleted."""
    try:
        client: aioredis.Redis = aioredis.from_url(REDIS_URL, decode_responses=False)
        deleted = 0
        batch: list[bytes] = []
        async for key in client.scan_iter(match=pattern, count=100):
            batch.append(key)
            if len(batch) >= 100:
                deleted += await client.delete(*batch)
                batch = []
        if batch:
            deleted += await client.delete(*batch)
        await client.aclose()
        return deleted
    except RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail=_build_error("cache_unavailable", "Cache backend unreachable."),
        ) from exc


async def _get_model_names(authorization: str) -> list[str]:
    """Fetch the live model catalogue from LiteLLM and return list of model IDs."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{LITELLM_BASE_URL}/v1/models",
                headers={"Authorization": authorization},
            )
            if resp.is_success:
                return [m["id"] for m in resp.json().get("data", [])]
            return []
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=_build_error(
                "model_catalogue_unavailable",
                "Cannot validate model name — LiteLLM unreachable.",
            ),
        ) from exc


def _write_cache_audit(
    request_id: str,
    key_hash: str,
    scope: str,
    model: str,
    keys_deleted: int,
    status_code: int,
) -> None:
    """Write a metadata-only audit entry for a cache flush operation (constitution §II)."""
    event_type = "cache_flush_model" if scope == "model" else "cache_flush_all"
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "request_id": request_id,
        "key_hash": key_hash,
        "model": model,
        "keys_deleted": keys_deleted,
        "status_code": status_code,
    }
    audit.info(json.dumps(entry))


# ── Cache management endpoints ────────────────────────────────────────────────


@app.delete("/cache/flush", response_model=CacheFlushResult)
async def cache_flush(
    request: Request,
    authorization: str = Header(default="", alias="Authorization"),
    model: str | None = Query(default=None, description="Flush only this model's cache entries"),
) -> CacheFlushResult:
    _validate_master_key(authorization)
    request_id: str = request.headers.get("x-request-id", "")
    key_hash: str = hashlib.sha256(authorization.encode()).hexdigest()

    if model is not None:
        valid_models = await _get_model_names(authorization)
        if model not in valid_models:
            raise HTTPException(
                status_code=422,
                detail=_build_error(
                    "invalid_model",
                    "Unknown model name.",
                    {"valid_models": valid_models},
                ),
            )
        count = await _redis_scan_del(f"llm_cache:{model}:*")
        _write_cache_audit(
            request_id=request_id,
            key_hash=key_hash,
            scope="model",
            model=model,
            keys_deleted=count,
            status_code=200,
        )
        return CacheFlushResult(keys_deleted=count, model=model)

    count = await _redis_scan_del("llm_cache:*")
    _write_cache_audit(
        request_id=request_id,
        key_hash=key_hash,
        scope="all",
        model="all",
        keys_deleted=count,
        status_code=200,
    )
    return CacheFlushResult(keys_deleted=count)


# ── Spend endpoints ───────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/spend", response_model=SpendReport)
async def get_spend(
    authorization: str = Header(..., alias="Authorization"),
    key_id: str | None = Query(default=None, description="Filter by key alias or key hash"),
) -> SpendReport:
    headers = {"Authorization": authorization}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            keys_resp, models_resp = await _fetch_spend_data(client, headers)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=_build_error("spend_store_unavailable", "Spend data temporarily unavailable"),
        ) from exc

    _check_auth_errors(keys_resp, models_resp)
    _check_upstream_errors(keys_resp, models_resp)

    by_model = _aggregate_models(models_resp.json())
    by_key = _aggregate_keys(keys_resp.json(), key_id)
    total = sum(m.spend_usd for m in by_model)
    period_start, period_end = _current_period()

    return SpendReport(
        period_start=period_start,
        period_end=period_end,
        total_spend_usd=round(total, 9),
        by_model=by_model,
        by_key=by_key,
    )


# ── Internal helpers ──────────────────────────────────────────────────────────


async def _fetch_spend_data(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> tuple[httpx.Response, httpx.Response]:
    keys_resp = await client.get(f"{LITELLM_BASE_URL}/global/spend/keys", headers=headers)
    models_resp = await client.get(f"{LITELLM_BASE_URL}/global/spend/models", headers=headers)
    return keys_resp, models_resp


def _check_auth_errors(keys_resp: httpx.Response, models_resp: httpx.Response) -> None:
    if keys_resp.status_code == 401 or models_resp.status_code == 401:
        raise HTTPException(
            status_code=401,
            detail=_build_error("unauthorized", "Master key required to access spend report"),
        )


def _check_upstream_errors(keys_resp: httpx.Response, models_resp: httpx.Response) -> None:
    if not (keys_resp.is_success and models_resp.is_success):
        raise HTTPException(
            status_code=503,
            detail=_build_error("spend_store_unavailable", "Spend data temporarily unavailable"),
        )


def _aggregate_models(data: list[dict[str, Any]]) -> list[ModelSpend]:
    result: list[ModelSpend] = []
    for row in data:
        result.append(
            ModelSpend(
                model=row.get("model", "unknown"),
                spend_usd=float(row.get("spend", 0.0)),
                prompt_tokens=int(row.get("prompt_tokens", 0)),
                completion_tokens=int(row.get("completion_tokens", 0)),
            )
        )
    return result


def _aggregate_keys(data: list[dict[str, Any]], key_id: str | None) -> list[KeySpend]:
    result: list[KeySpend] = []
    for row in data:
        alias: str | None = row.get("key_alias")
        token: str = row.get("token", "")
        spend = float(row.get("spend", 0.0))
        max_budget: float | None = row.get("max_budget")
        budget_remaining: float | None = (
            round(max_budget - spend, 9) if max_budget is not None else None
        )

        key_entry = KeySpend(
            key_alias=alias,
            key_hash=_mask_key(token),
            spend_usd=spend,
            max_budget_usd=max_budget,
            budget_remaining_usd=budget_remaining,
            budget_reset_at=row.get("budget_reset_at"),
        )

        if key_id is None or alias == key_id or token.endswith(key_id):
            result.append(key_entry)

    return result


# ── Error handler ─────────────────────────────────────────────────────────────


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=_build_error(str(exc.status_code), str(exc.detail)),
    )
