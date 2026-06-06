"""Async PostgreSQL pool and CRUD helpers for the batch database."""
from __future__ import annotations

import json
from typing import Any

import asyncpg

_pool: asyncpg.Pool | None = None

_DDL = """
CREATE TABLE IF NOT EXISTS batch_jobs (
    job_id             TEXT        PRIMARY KEY,
    consumer_id        TEXT        NOT NULL,
    model              TEXT        NOT NULL,
    status             TEXT        NOT NULL DEFAULT 'queued'
                       CHECK (status IN ('queued', 'running', 'completed')),
    total_items        INT         NOT NULL CHECK (total_items BETWEEN 1 AND 10000),
    completed_items    INT         NOT NULL DEFAULT 0,
    failed_items       INT         NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at         TIMESTAMPTZ,
    completed_at       TIMESTAMPTZ,
    results_expires_at TIMESTAMPTZ,
    results_path       TEXT,
    trace_id           TEXT        NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_batch_jobs_consumer ON batch_jobs (consumer_id);
CREATE INDEX IF NOT EXISTS idx_batch_jobs_status   ON batch_jobs (status);
CREATE INDEX IF NOT EXISTS idx_batch_jobs_expires  ON batch_jobs (results_expires_at)
    WHERE results_expires_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS batch_items (
    id              BIGSERIAL   PRIMARY KEY,
    job_id          TEXT        NOT NULL REFERENCES batch_jobs(job_id)
                    ON DELETE CASCADE,
    item_index      INT         NOT NULL,
    input_payload   JSONB,
    output_payload  JSONB,
    status          TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'success', 'error')),
    error_detail    TEXT,
    retry_count     INT         NOT NULL DEFAULT 0,
    latency_ms      INT,
    processed_at    TIMESTAMPTZ,
    UNIQUE (job_id, item_index)
);

CREATE INDEX IF NOT EXISTS idx_batch_items_job_status ON batch_items (job_id, status);
"""


async def init_pool(dsn: str) -> None:
    global _pool
    _pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call init_pool() first")
    return _pool


async def create_schema() -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(_DDL)


async def insert_job(
    job_id: str,
    consumer_id: str,
    model: str,
    total_items: int,
    trace_id: str,
) -> None:
    await get_pool().execute(
        """
        INSERT INTO batch_jobs (job_id, consumer_id, model, total_items, trace_id)
        VALUES ($1, $2, $3, $4, $5)
        """,
        job_id, consumer_id, model, total_items, trace_id,
    )


async def insert_items(job_id: str, items: list[dict[str, Any]]) -> None:
    records = [
        (job_id, item["item_index"], json.dumps(item["input_payload"]))
        for item in items
    ]
    await get_pool().executemany(
        """
        INSERT INTO batch_items (job_id, item_index, input_payload)
        VALUES ($1, $2, $3::jsonb)
        """,
        records,
    )


async def get_job(job_id: str) -> asyncpg.Record | None:
    return await get_pool().fetchrow(
        "SELECT * FROM batch_jobs WHERE job_id = $1", job_id
    )


async def get_pending_items(job_id: str) -> list[asyncpg.Record]:
    return await get_pool().fetch(
        """
        SELECT item_index, input_payload
        FROM batch_items
        WHERE job_id = $1 AND status = 'pending'
        ORDER BY item_index
        """,
        job_id,
    )


async def update_job_status(job_id: str, status: str, **kwargs: Any) -> None:
    sets = ["status = $2"]
    values: list[Any] = [job_id, status]
    for key, val in kwargs.items():
        sets.append(f"{key} = ${len(values) + 1}")
        values.append(val)
    await get_pool().execute(
        f"UPDATE batch_jobs SET {', '.join(sets)} WHERE job_id = $1",
        *values,
    )


async def update_item_status(
    job_id: str,
    item_index: int,
    status: str,
    error_detail: str | None,
    latency_ms: int,
) -> None:
    await get_pool().execute(
        """
        UPDATE batch_items
        SET status = $3, error_detail = $4, latency_ms = $5,
            processed_at = now(), retry_count = retry_count + 1,
            input_payload = NULL
        WHERE job_id = $1 AND item_index = $2
        """,
        job_id, item_index, status, error_detail, latency_ms,
    )


async def update_job_counters(
    job_id: str, success_delta: int, fail_delta: int
) -> None:
    await get_pool().execute(
        """
        UPDATE batch_jobs
        SET completed_items = completed_items + $2,
            failed_items    = failed_items    + $3
        WHERE job_id = $1
        """,
        job_id, success_delta, fail_delta,
    )


async def get_expired_jobs() -> list[asyncpg.Record]:
    return await get_pool().fetch(
        """
        SELECT job_id, results_path
        FROM batch_jobs
        WHERE results_expires_at < now() AND results_path IS NOT NULL
        """
    )


async def clear_results_path(job_id: str) -> None:
    await get_pool().execute(
        "UPDATE batch_jobs SET results_path = NULL WHERE job_id = $1", job_id
    )


async def null_stale_payloads() -> None:
    await get_pool().execute(
        """
        UPDATE batch_items
        SET input_payload = NULL
        WHERE processed_at < now() - INTERVAL '2 hours'
          AND input_payload IS NOT NULL
        """
    )
