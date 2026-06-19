"""Staging_Updater: DB helper for updating news_staging classification columns.

Requirements: 5.1–5.6
"""

from __future__ import annotations

import logging

import asyncpg

from app.pipeline.orchestrator import PipelineSummary

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helper — no I/O, fully unit-testable
# ---------------------------------------------------------------------------

def determine_status(summary: PipelineSummary) -> str:
    """Return the classification status string for a given PipelineSummary.

    Status logic:
      - "complete" : db_inserted > 0 AND db_failures == 0 AND unresolved == 0
      - "partial"  : db_inserted > 0 AND (db_failures > 0 OR unresolved > 0)
      - "failed"   : db_inserted == 0 (any total) OR total == 0

    The three cases are mutually exclusive and exhaustive over all
    non-negative integer combinations.
    """
    if summary.total == 0:
        return "failed"
    if summary.db_inserted > 0 and summary.db_failures == 0 and summary.unresolved == 0:
        return "complete"
    if summary.db_inserted > 0 and (summary.db_failures > 0 or summary.unresolved > 0):
        return "partial"
    # db_inserted == 0 and total > 0
    return "failed"


# ---------------------------------------------------------------------------
# Staging_Updater
# ---------------------------------------------------------------------------

class Staging_Updater:
    """Updates classification-tracking columns on news_staging after a pipeline run."""

    def __init__(self, pool: asyncpg.Pool, schema: str, event_schema: str = "stoxscoop_dev") -> None:
        self._pool = pool
        self._schema = schema          # schema containing news_staging
        self._event_schema = event_schema  # schema containing events table

    async def get_cumulative_loaded(
        self,
        batch_id: int,
        source_url: str,
    ) -> int:
        """Return the count of events already inserted for this batch + URL.

        Used on retry runs to compute the true cumulative stocks_loaded.
        """
        sql = (
            f"SELECT COUNT(*) FROM {self._event_schema}.events"
            " WHERE batch_id = $1 AND source_url = $2"
        )
        async with self._pool.acquire() as conn:
            count = await conn.fetchval(sql, batch_id, source_url)
        return int(count or 0)

    async def update_classification_status(
        self,
        staging_id: int,
        batch_id: int,
        summary: PipelineSummary,
        url: str,
        is_retry: bool = False,
    ) -> None:
        """Update news_staging classification columns for a single record.

        On retry (is_retry=True), queries the events table for the cumulative
        stocks_loaded count rather than using summary.db_inserted alone.

        Requirements: 5.1–5.6
        """
        status = determine_status(summary)

        if is_retry:
            # Cumulative count from DB for retry runs (Req 5.6)
            stocks_loaded = await self.get_cumulative_loaded(batch_id, url)
            stocks_failed = max(0, summary.total - stocks_loaded)
        else:
            stocks_loaded = summary.db_inserted
            stocks_failed = summary.db_failures + summary.unresolved

        sql = (
            f"UPDATE {self._schema}.news_staging"
            " SET event_batch_id = $1,"
            "     stocks_loaded = $2,"
            "     stocks_failed = $3,"
            "     classification_status = $4,"
            "     is_loaded = $5"
            " WHERE id = $6"
        )
        is_loaded = (status == "complete")
        async with self._pool.acquire() as conn:
            await conn.execute(sql, batch_id, stocks_loaded, stocks_failed, status, is_loaded, staging_id)

        logger.debug(
            "Updated news_staging id=%d: batch_id=%d status=%s loaded=%d failed=%d",
            staging_id, batch_id, status, stocks_loaded, stocks_failed,
        )
