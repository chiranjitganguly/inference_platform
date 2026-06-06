"""Background cleanup jobs for the batch-worker service.

Scheduled via APScheduler in main.py lifespan:
  - expire_results: every 5 minutes — delete JSONL files past retention window
  - safety_net_cleanup: every 2 hours — NULL out stale input_payload columns
"""
from __future__ import annotations

import logging
import pathlib

import asyncpg

logger = logging.getLogger(__name__)


async def expire_results(pool: asyncpg.Pool) -> None:
    """Delete result files for jobs past their 24-hour retention window."""
    rows = await pool.fetch(
        """
        SELECT job_id, results_path
        FROM batch_jobs
        WHERE results_expires_at < now() AND results_path IS NOT NULL
        """
    )
    for row in rows:
        path = pathlib.Path(row["results_path"])
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to delete result file %s: %s", path, exc)
        await pool.execute(
            "UPDATE batch_jobs SET results_path = NULL WHERE job_id = $1",
            row["job_id"],
        )
        logger.info("Expired results for job %s", row["job_id"])


async def safety_net_cleanup(pool: asyncpg.Pool) -> None:
    """NULL out input_payload for items processed more than 2 hours ago (safety net)."""
    result = await pool.execute(
        """
        UPDATE batch_items
        SET input_payload = NULL
        WHERE processed_at < now() - INTERVAL '2 hours'
          AND input_payload IS NOT NULL
        """
    )
    logger.info("safety_net_cleanup: %s", result)
