"""RQ job function for async batch inference processing.

Each batch job is a single RQ task. Inside the job function, an asyncio event
loop is created and items are processed concurrently, bounded by MAX_CONCURRENT.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import db
import httpx
import otel
from opentelemetry.trace import SpanKind, StatusCode

logger = logging.getLogger(__name__)

LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_MASTER_KEY = os.getenv("LITELLM_MASTER_KEY", "")
RESULTS_DIR = pathlib.Path(os.getenv("RESULTS_DIR", "/results"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "4"))
_MAX_RETRIES = 3


# ── RQ entry point ─────────────────────────────────────────────────────────────


def process_batch(job_id: str) -> None:
    """Synchronous RQ entry point — delegates to asyncio event loop."""
    asyncio.run(_run(job_id))


# ── Async orchestrator ─────────────────────────────────────────────────────────


async def _run(job_id: str) -> None:
    pool = await asyncpg.create_pool(
        dsn=os.environ["BATCH_DATABASE_URL"], min_size=2, max_size=MAX_CONCURRENT + 2
    )
    otel.init_tracer()
    try:
        await _process(job_id, pool)
    finally:
        await pool.close()


async def _process(job_id: str, pool: asyncpg.Pool) -> None:
    now = datetime.now(tz=timezone.utc)
    await pool.execute(
        "UPDATE batch_jobs SET status = 'running', started_at = $2 WHERE job_id = $1",
        job_id,
        now,
    )

    job_row = await pool.fetchrow(
        "SELECT model, trace_id FROM batch_jobs WHERE job_id = $1", job_id
    )
    if job_row is None:
        logger.error("Job %s not found in DB", job_id)
        return

    model: str = job_row["model"]
    parent_ctx = otel.extract_context(job_row["trace_id"] or "")

    items = await pool.fetch(
        """
        SELECT item_index, input_payload
        FROM batch_items
        WHERE job_id = $1 AND status = 'pending'
        ORDER BY item_index
        """,
        job_id,
    )

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    results: list[dict[str, Any]] = []
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    headers = {"Authorization": f"Bearer {LITELLM_MASTER_KEY}"} if LITELLM_MASTER_KEY else {}
    async with httpx.AsyncClient(base_url=LITELLM_BASE_URL, timeout=120.0, headers=headers) as client:
        await asyncio.gather(
            *(
                _process_item(
                    job_id=job_id,
                    item_index=int(item["item_index"]),
                    input_payload=json.loads(item["input_payload"])
                    if isinstance(item["input_payload"], str)
                    else dict(item["input_payload"]),
                    model=model,
                    sem=sem,
                    client=client,
                    pool=pool,
                    results=results,
                    parent_ctx=parent_ctx,
                )
                for item in items
            )
        )

    results.sort(key=lambda r: r["index"])
    result_path = RESULTS_DIR / f"{job_id}.jsonl"
    with result_path.open("w", encoding="utf-8") as fh:
        for record in results:
            fh.write(json.dumps(record) + "\n")

    completed_at = datetime.now(tz=timezone.utc)
    expires_at = completed_at + timedelta(hours=24)
    await pool.execute(
        """
        UPDATE batch_jobs
        SET status = 'completed',
            completed_at = $2,
            results_expires_at = $3,
            results_path = $4
        WHERE job_id = $1
        """,
        job_id,
        completed_at,
        expires_at,
        str(result_path),
    )
    logger.info(
        "Job %s completed: %d results written to %s", job_id, len(results), result_path
    )


# ── Per-item processor ─────────────────────────────────────────────────────────


async def _process_item(
    *,
    job_id: str,
    item_index: int,
    input_payload: dict[str, Any],
    model: str,
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    pool: asyncpg.Pool,
    results: list[dict[str, Any]],
    parent_ctx: Any,
) -> None:
    tracer = otel.get_tracer()
    with tracer.start_as_current_span(
        "batch_item", context=parent_ctx, kind=SpanKind.CLIENT
    ) as span:
        span.set_attribute("batch_job_id", job_id)
        span.set_attribute("item_index", item_index)
        span.set_attribute("model", model)

        status = "error"
        error_detail: str | None = None
        output: dict[str, Any] | None = None
        latency_ms = 0

        async with sem:
            t0 = time.monotonic()
            logger.info(
                "litellm_call",
                extra={"job_id": job_id, "item_index": item_index, "event": "start"},
            )
            try:
                status, output, error_detail = await _call_litellm_with_retry(
                    client=client,
                    model=model,
                    messages=input_payload.get("messages", []),
                    job_id=job_id,
                    item_index=item_index,
                )
            except Exception as exc:
                error_detail = f"unexpected error: {exc}"
                status = "error"
            finally:
                latency_ms = int((time.monotonic() - t0) * 1000)
                logger.info(
                    "litellm_call",
                    extra={"job_id": job_id, "item_index": item_index, "event": "end"},
                )

        span.set_attribute("status", status)
        span.set_attribute("latency_ms", latency_ms)
        if status == "error":
            span.set_status(StatusCode.ERROR, error_detail or "unknown error")
            if error_detail:
                span.record_exception(Exception(error_detail))
        else:
            span.set_status(StatusCode.OK)

    try:
        await pool.execute(
            """
            UPDATE batch_items
            SET status = $3, error_detail = $4, latency_ms = $5,
                processed_at = now(), input_payload = NULL
            WHERE job_id = $1 AND item_index = $2
            """,
            job_id,
            item_index,
            status,
            error_detail,
            latency_ms,
        )
        if status == "success":
            await db.update_job_counters(job_id, 1, 0)
        else:
            await db.update_job_counters(job_id, 0, 1)
    except Exception as exc:
        logger.error("DB update failed for job %s item %d: %s", job_id, item_index, exc)

    record: dict[str, Any] = {
        "index": item_index,
        "status": status,
        "latency_ms": latency_ms,
    }
    if status == "success" and output is not None:
        record["output"] = output
    else:
        record["error_detail"] = error_detail or "unknown error"
    results.append(record)


async def _call_litellm_with_retry(
    *,
    client: httpx.AsyncClient,
    model: str,
    messages: list[dict[str, str]],
    job_id: str,
    item_index: int,
) -> tuple[str, dict[str, Any] | None, str | None]:
    last_error: str = "no attempts made"
    for attempt in range(_MAX_RETRIES):
        try:
            resp = await client.post(
                "/v1/chat/completions",
                json={"model": model, "messages": messages},
            )
            if resp.is_success:
                return "success", resp.json(), None
            if resp.status_code < 500:
                # 4xx — permanent error, no retry
                return "error", None, f"LiteLLM {resp.status_code}: {resp.text[:200]}"
            # 5xx — transient, retry
            last_error = f"LiteLLM {resp.status_code}: {resp.text[:200]}"
        except (httpx.TransientError, httpx.TimeoutException) as exc:
            last_error = f"network error: {exc}"
        except Exception as exc:
            return "error", None, f"unexpected error: {exc}"

        if attempt < _MAX_RETRIES - 1:
            await asyncio.sleep(2**attempt)

    return "error", None, f"failed after {_MAX_RETRIES} attempts: {last_error}"
