"""batch-worker — async batch inference API for the AI Inference Platform."""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import db
import otel
import redis as redis_sync
import rq
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from cleanup import expire_results, safety_net_cleanup
from fastapi import FastAPI, Header, HTTPException, Path
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, field_validator
from worker import process_batch

logger = logging.getLogger(__name__)

REDIS_QUEUE_URL = os.getenv("REDIS_QUEUE_URL", "redis://redis-queue:6380")
MAX_BATCH_SIZE = 10_000

_scheduler: AsyncIOScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    global _scheduler
    await db.init_pool(os.environ["BATCH_DATABASE_URL"])
    await db.create_schema()
    otel.init_tracer()
    pool = db.get_pool()
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(expire_results, "interval", minutes=5, args=[pool])
    _scheduler.add_job(safety_net_cleanup, "interval", hours=2, args=[pool])
    _scheduler.start()
    yield
    _scheduler.shutdown(wait=False)


app = FastAPI(title="batch-worker", version="1.0.0", lifespan=lifespan)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_error(
    error: str, message: str, detail: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"error": error, "message": message, "detail": detail or {}}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _row_to_iso(val: datetime | None) -> str | None:
    if val is None:
        return None
    return val.isoformat().replace("+00:00", "Z")


# ── Request / Response models ─────────────────────────────────────────────────


class BatchItemInput(BaseModel):
    index: int
    messages: list[dict[str, str]]


class BatchSubmitRequest(BaseModel):
    model: str
    items: list[BatchItemInput]

    @field_validator("items")
    @classmethod
    def validate_items(cls, v: list[BatchItemInput]) -> list[BatchItemInput]:
        if len(v) == 0:
            raise ValueError("items array must not be empty")
        if len(v) > MAX_BATCH_SIZE:
            raise ValueError(
                f"Batch size {len(v)} exceeds limit of {MAX_BATCH_SIZE} items"
            )
        indices = sorted(item.index for item in v)
        expected = list(range(len(v)))
        if indices != expected:
            raise ValueError(
                "item indices must be contiguous 0..N-1 with no duplicates or gaps"
            )
        return v


class JobCreatedResponse(BaseModel):
    job_id: str
    status: str
    total_items: int
    created_at: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    model: str
    total_items: int
    completed_items: int
    failed_items: int
    created_at: str
    started_at: str | None
    completed_at: str | None
    results_expires_at: str | None


# ── Endpoints ─────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/batch/jobs", status_code=202)
async def submit_batch(
    request: BatchSubmitRequest,
    x_consumer_custom_id: str = Header(default=""),
) -> JSONResponse:
    job_id = str(uuid.uuid4())
    tracer = otel.get_tracer()

    with tracer.start_as_current_span("batch_job") as span:
        span.set_attribute("batch_job_id", job_id)
        span.set_attribute("model", request.model)
        span.set_attribute("total_items", len(request.items))
        trace_id = otel.inject_context(span)

    await db.insert_job(
        job_id=job_id,
        consumer_id=x_consumer_custom_id,
        model=request.model,
        total_items=len(request.items),
        trace_id=trace_id,
    )
    await db.insert_items(
        job_id=job_id,
        items=[
            {"item_index": item.index, "input_payload": {"messages": item.messages}}
            for item in request.items
        ],
    )

    redis_conn = redis_sync.from_url(REDIS_QUEUE_URL)
    queue = rq.Queue("batch", connection=redis_conn)
    queue.enqueue(process_batch, job_id)

    return JSONResponse(
        status_code=202,
        content=JobCreatedResponse(
            job_id=job_id,
            status="queued",
            total_items=len(request.items),
            created_at=_now_iso(),
        ).model_dump(),
    )


@app.get("/v1/batch/jobs/{job_id}")
async def get_job_status(
    job_id: str = Path(...),
    x_consumer_custom_id: str = Header(default=""),
) -> JobStatusResponse:
    row = await db.get_job(job_id)
    if row is None or row["consumer_id"] != x_consumer_custom_id:
        raise HTTPException(
            status_code=404,
            detail=_build_error("not_found", "Job not found", {"job_id": job_id}),
        )
    return JobStatusResponse(
        job_id=row["job_id"],
        status=row["status"],
        model=row["model"],
        total_items=row["total_items"],
        completed_items=row["completed_items"],
        failed_items=row["failed_items"],
        created_at=_row_to_iso(row["created_at"]) or "",
        started_at=_row_to_iso(row["started_at"]),
        completed_at=_row_to_iso(row["completed_at"]),
        results_expires_at=_row_to_iso(row["results_expires_at"]),
    )


@app.get("/v1/batch/jobs/{job_id}/results")
async def get_job_results(
    job_id: str = Path(...),
    x_consumer_custom_id: str = Header(default=""),
) -> FileResponse:
    row = await db.get_job(job_id)
    if row is None or row["consumer_id"] != x_consumer_custom_id:
        raise HTTPException(
            status_code=404,
            detail=_build_error("not_found", "Job not found", {"job_id": job_id}),
        )

    if row["status"] != "completed":
        raise HTTPException(
            status_code=409,
            detail=_build_error(
                "job_not_complete",
                "Job results are not yet available",
                {
                    "job_id": job_id,
                    "status": row["status"],
                    "completed_items": row["completed_items"],
                    "total_items": row["total_items"],
                },
            ),
        )

    expires_at: datetime | None = row["results_expires_at"]
    if expires_at is not None and expires_at < datetime.now(tz=timezone.utc):
        raise HTTPException(
            status_code=410,
            detail=_build_error(
                "results_expired",
                "Results have expired and been deleted",
                {"job_id": job_id, "expired_at": _row_to_iso(expires_at)},
            ),
        )

    results_path: str | None = row["results_path"]
    if not results_path:
        raise HTTPException(
            status_code=410,
            detail=_build_error(
                "results_expired",
                "Results have expired and been deleted",
                {"job_id": job_id},
            ),
        )

    return FileResponse(
        path=results_path,
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="batch-{job_id[:8]}.jsonl"'
        },
    )


# ── Error handler ─────────────────────────────────────────────────────────────


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Any, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=_build_error(str(exc.status_code), str(exc.detail)),
    )
