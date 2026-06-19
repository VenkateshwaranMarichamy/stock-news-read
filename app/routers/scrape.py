"""Scrape router — POST /scrape.

Requirements: 6.1–6.7, 7.1–7.5, 8.1–8.3
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.db.writer import DB_Writer
from app.schemas import ScrapeRequest, ScrapeResponse
from moneycontrol_scraper.config import filter_sections
from moneycontrol_scraper.exceptions import ScraperFetchError
from moneycontrol_scraper.http_client import HTTPClient
from moneycontrol_scraper.models import OutputRecord
from moneycontrol_scraper.parser import ArticleParser
from moneycontrol_scraper.serialiser import serialise, write_output

logger = logging.getLogger(__name__)

router = APIRouter()


def _run_scraper(
    urls: list[str], delay: float, section_whitelist: list[str]
) -> tuple[list[OutputRecord], list[dict]]:
    """Run the synchronous scraper for each URL."""
    http_client = HTTPClient()
    parser = ArticleParser()
    records: list[OutputRecord] = []
    failures: list[dict] = []

    for i, url in enumerate(urls):
        try:
            html = http_client.fetch(url)
            record = parser.parse(html, url)
            if section_whitelist:
                record.sections = filter_sections(record.sections, section_whitelist)
            records.append(record)
        except ScraperFetchError as exc:
            logger.error("Failed to fetch %s: %s", url, exc)
            failures.append({"url": url, "reason": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to parse %s: %s", url, exc)
            failures.append({"url": url, "reason": str(exc)})

        if i < len(urls) - 1:
            time.sleep(delay)

    return records, failures


def _count_stock_entries(record: OutputRecord) -> int:
    """Count total stock entries (leaf keys) across all sections."""
    return sum(len(stocks) for stocks in record.sections.values())


@router.post("/scrape", response_model=ScrapeResponse)
async def scrape(request: ScrapeRequest, req: Request) -> ScrapeResponse:
    """Trigger scraping of one or more MoneyControl URLs."""
    config = req.app.state.config
    pool = getattr(req.app.state, "pool", None)
    schema = getattr(req.app.state, "schema", None)
    valid_subtypes = getattr(req.app.state, "valid_subtypes", None)

    url_strings = [str(u) for u in request.urls]

    loop = asyncio.get_event_loop()
    records, failures = await loop.run_in_executor(
        None, _run_scraper, url_strings, config.delay, config.sections
    )

    if not records and failures:
        raise HTTPException(status_code=502, detail={"failures": failures})

    inserted = 0
    skipped = 0
    events_queued = 0

    output_mode = config.output_mode

    # --- File output --------------------------------------------------------
    if output_mode in {"file", "both"} and records:
        out_dir = Path(config.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_path = str(out_dir / f"{timestamp}.json")
        try:
            write_output(serialise(records), output_path)
            logger.info("Wrote %d records to %s", len(records), output_path)
        except Exception as exc:
            logger.error("File write failed: %s", exc)

    # --- Database output ----------------------------------------------------
    if output_mode in {"database", "both"} and records and pool is not None:
        writer = DB_Writer(pool, schema)
        inserted, skipped, url_to_staging_id = await writer.write_batch(records)

        # --- Pipeline integration -------------------------------------------
        # Run for all records that have stock entries, not just newly inserted ones.
        # Already-existing records (skipped) also need classification if not yet done.
        if valid_subtypes is not None:
            from app.pipeline.resolver import Stock_Name_Resolver
            from app.pipeline.classifier import Event_Classifier
            from app.pipeline.event_writer import Event_DB_Writer
            from app.pipeline.orchestrator import Pipeline_Orchestrator
            from app.db.staging_updater import Staging_Updater

            for record in records:
                stock_entry_count = _count_stock_entries(record)
                if stock_entry_count == 0:
                    continue

                events_queued += stock_entry_count
                staging_id = url_to_staging_id.get(record.url)

                # For skipped (duplicate) records, look up the existing staging_id
                if staging_id is None:
                    try:
                        async with pool.acquire() as conn:
                            row = await conn.fetchrow(
                                f"SELECT id, classification_status FROM {schema}.news_staging"
                                " WHERE url = $1",
                                record.url,
                            )
                        if row:
                            staging_id = row["id"]
                            # Skip if already fully classified
                            if row["classification_status"] == "complete":
                                logger.info(
                                    "Skipping pipeline for already-complete record id=%d url=%s",
                                    staging_id, record.url,
                                )
                                continue
                    except Exception as exc:
                        logger.warning("Could not look up staging_id for %s: %s", record.url, exc)

                batch_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
                batch_name = f"moneycontrol_{batch_ts}"

                resolver = Stock_Name_Resolver(pool, schema)
                classifier = Event_Classifier(valid_subtypes)
                event_writer = Event_DB_Writer(pool, schema)
                orchestrator = Pipeline_Orchestrator(resolver, classifier, event_writer)
                staging_updater = Staging_Updater(pool, schema)

                async def _run_pipeline(
                    _orchestrator=orchestrator,
                    _resolver=resolver,
                    _record=record,
                    _batch_name=batch_name,
                    _staging_id=staging_id,
                    _staging_updater=staging_updater,
                ) -> None:
                    try:
                        await _resolver.load_cache()
                        summary, batch_id = await _orchestrator.run(_record, _batch_name)

                        # Update classification tracking on news_staging
                        if _staging_id is not None:
                            try:
                                await _staging_updater.update_classification_status(
                                    _staging_id, batch_id, summary, _record.url, is_retry=False
                                )
                            except Exception as upd_exc:
                                try:
                                    logger.error(
                                        "Failed to update staging status for id=%s: %s",
                                        _staging_id, upd_exc,
                                    )
                                except Exception:
                                    pass
                        else:
                            logger.warning(
                                "No staging_id for URL %s — skipping classification status update",
                                _record.url,
                            )
                    except Exception as exc:
                        logger.error(
                            "Pipeline background task failed for %s: %s",
                            _record.url, exc,
                        )

                asyncio.create_task(_run_pipeline())

    elif output_mode == "file" and records:
        inserted = len(records)
        skipped = 0
        events_queued = 0

    return ScrapeResponse(
        processed=len(url_strings),
        inserted=inserted,
        skipped=skipped,
        failures=failures,
        events_queued=events_queued,
    )
