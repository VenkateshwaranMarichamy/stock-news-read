"""Classify router — POST /classify.

Manually trigger event classification for one or more news_staging records
identified by their IDs. Runs synchronously (not as a background task) so
the caller receives the full result immediately.

Requirements: 1.1–1.5, 2.1–2.4, 3.1–3.4, 4.1–4.4, 5.1–5.6, 6.1–6.3,
              9.1–9.5
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import asyncpg
from fastapi import APIRouter, HTTPException, Request

from app.db.staging_updater import Staging_Updater, determine_status
from app.pipeline.classifier import Event_Classifier
from app.pipeline.event_writer import Event_DB_Writer
from app.pipeline.orchestrator import Pipeline_Orchestrator, PipelineSummary
from app.pipeline.resolver import Stock_Name_Resolver
from app.schemas import ClassifyRequest, ClassifyResponse
from moneycontrol_scraper.models import OutputRecord

logger = logging.getLogger(__name__)

router = APIRouter()


def _count_stock_entries_from_content(content: dict | None) -> int:
    """Count total stock entries (leaf keys) across all sections in content."""
    if not content:
        return 0
    return sum(len(stocks) for stocks in content.values() if isinstance(stocks, dict))


def _make_failed_summary() -> PipelineSummary:
    """Return a zero-count PipelineSummary representing a failed/skipped record."""
    return PipelineSummary(
        total=0,
        resolved=0,
        unresolved=0,
        classified=0,
        classification_failures=0,
        db_inserted=0,
        db_failures=0,
    )


@router.post("/classify", response_model=ClassifyResponse)
async def classify(request: ClassifyRequest, req: Request) -> ClassifyResponse:
    """Manually trigger event classification for one or more news_staging records.

    - Deduplicates input IDs (preserves first occurrence).
    - Loads content from news_staging for each ID.
    - Reuses existing event_batch_id on retry; creates a new batch otherwise.
    - Runs Pipeline_Orchestrator synchronously per record.
    - Updates news_staging classification columns after each run.
    - Returns a structured ClassifyResponse.
    """
    pool = getattr(req.app.state, "pool", None)
    schema = getattr(req.app.state, "schema", None)
    valid_subtypes = getattr(req.app.state, "valid_subtypes", None)

    if pool is None:
        raise HTTPException(status_code=503, detail="database connectivity issue")

    # Step 1: Deduplicate IDs preserving first occurrence (Req 1.5)
    seen: set[int] = set()
    deduped_ids: list[int] = []
    for id_ in request.ids:
        if id_ not in seen:
            seen.add(id_)
            deduped_ids.append(id_)

    # Step 2: Bulk SELECT from news_staging (Req 2.1)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT id, url, content, published_date, event_batch_id"
                f" FROM {schema}.news_staging"
                f" WHERE id = ANY($1::int[])",
                deduped_ids,
            )
    except Exception as exc:
        logger.error("Failed to load staging records: %s", exc)
        raise HTTPException(status_code=503, detail="database connectivity issue")

    # Build lookup: id → row
    found: dict[int, asyncpg.Record] = {row["id"]: row for row in rows}

    ids_processed: list[int] = []
    ids_missing: list[int] = []
    ids_skipped: list[int] = []
    total_stocks = 0
    events_inserted = 0
    events_failed = 0
    unresolved_stocks = 0

    staging_updater = Staging_Updater(pool, schema)

    # Import pipeline components once
    from app.pipeline.resolver import Stock_Name_Resolver
    from app.pipeline.classifier import Event_Classifier
    from app.pipeline.event_writer import Event_DB_Writer

    # Load resolver cache once for all records in this request
    resolver = Stock_Name_Resolver(pool, schema)
    try:
        await resolver.load_cache()
    except RuntimeError as exc:
        logger.error("Failed to load ticker cache: %s", exc)
        raise HTTPException(status_code=503, detail="database connectivity issue")

    classifier = Event_Classifier(valid_subtypes or {})
    event_writer = Event_DB_Writer(pool, schema)
    orchestrator = Pipeline_Orchestrator(resolver, classifier, event_writer)

    for id_ in deduped_ids:
        # Step 3: Handle missing IDs (Req 2.2)
        if id_ not in found:
            ids_missing.append(id_)
            continue

        row = found[id_]
        url: str = row["url"]
        published_date = row["published_date"]
        existing_batch_id: int | None = row["event_batch_id"]

        # Parse content
        raw_content = row["content"]
        if isinstance(raw_content, str):
            try:
                content = json.loads(raw_content)
            except (json.JSONDecodeError, TypeError):
                content = None
        else:
            content = raw_content  # asyncpg returns dict for JSONB

        stock_count = _count_stock_entries_from_content(content)

        # Step 4: Skip records with no content (Req 2.3)
        if not content or stock_count == 0:
            ids_skipped.append(id_)
            try:
                # Set status=failed for empty content records
                if existing_batch_id is not None:
                    await staging_updater.update_classification_status(
                        id_, existing_batch_id, _make_failed_summary(), url, is_retry=False
                    )
                else:
                    # No batch yet — just update status directly
                    async with pool.acquire() as conn:
                        await conn.execute(
                            f"UPDATE {schema}.news_staging"
                            " SET classification_status = 'failed'"
                            " WHERE id = $1",
                            id_,
                        )
            except Exception as upd_exc:
                try:
                    logger.error("Failed to update status for skipped id=%d: %s", id_, upd_exc)
                except Exception:
                    pass
            continue

        # Build an OutputRecord-like object from staging content
        from moneycontrol_scraper.models import OutputRecord
        published_date_str: str | None = None
        if published_date is not None:
            try:
                published_date_str = published_date.strftime("%Y-%m-%d")
            except AttributeError:
                published_date_str = str(published_date)[:10]

        record = OutputRecord(
            date=published_date_str,
            url=url,
            sections=content,
        )

        # Step 5: Determine batch_id (Req 3.1, 3.2)
        is_retry = existing_batch_id is not None
        batch_id: int | None = existing_batch_id

        if not is_retry:
            # Create a new batch
            batch_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
            batch_name = f"moneycontrol_{batch_ts}"
            try:
                batch_id = await event_writer.start_batch(batch_name)
            except Exception as exc:
                logger.error("start_batch failed for staging id=%d: %s", id_, exc)
                ids_skipped.append(id_)
                try:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            f"UPDATE {schema}.news_staging"
                            " SET classification_status = 'failed'"
                            " WHERE id = $1",
                            id_,
                        )
                except Exception:
                    pass
                continue

        # Step 6: Run pipeline (Req 4.1–4.4)
        try:
            summary, batch_id = await orchestrator.run_with_batch_id(record, batch_id)
        except Exception as exc:
            logger.error("Pipeline failed for staging id=%d url=%s: %s", id_, url, exc)
            ids_skipped.append(id_)
            # Update status to failed
            try:
                await staging_updater.update_classification_status(
                    id_, batch_id, _make_failed_summary(), url, is_retry=is_retry
                )
            except Exception as upd_exc:
                try:
                    logger.error("Failed to update status after pipeline error id=%d: %s", id_, upd_exc)
                except Exception:
                    pass
            continue

        # Step 7: Update classification status (Req 5.1–5.6)
        try:
            await staging_updater.update_classification_status(
                id_, batch_id, summary, url, is_retry=is_retry
            )
        except Exception as upd_exc:
            try:
                logger.error("Failed to update staging status for id=%d: %s", id_, upd_exc)
            except Exception:
                pass
            # Continue — do not alter HTTP response (Req 9.3)

        # Accumulate response counts (Req 6.2)
        ids_processed.append(id_)
        total_stocks += summary.total
        events_inserted += summary.db_inserted
        events_failed += summary.db_failures
        unresolved_stocks += summary.unresolved

    return ClassifyResponse(
        ids_processed=ids_processed,
        ids_missing=ids_missing,
        ids_skipped=ids_skipped,
        total_stocks=total_stocks,
        events_inserted=events_inserted,
        events_failed=events_failed,
        unresolved_stocks=unresolved_stocks,
    )
