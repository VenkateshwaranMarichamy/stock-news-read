"""Scrape router — POST /scrape.

Requirements: 6.1–6.7
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
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
    """Run the synchronous scraper for each URL.

    Runs in a thread pool executor (called via run_in_executor) so it does
    not block the async event loop.

    Returns:
        (records, failures) where failures is a list of
        {"url": str, "reason": str} dicts.
    """
    http_client = HTTPClient()
    parser = ArticleParser()
    records: list[OutputRecord] = []
    failures: list[dict] = []

    for i, url in enumerate(urls):
        try:
            html = http_client.fetch(url)
            record = parser.parse(html, url)
            # Apply section whitelist from config (same as CLI behaviour)
            if section_whitelist:
                record.sections = filter_sections(record.sections, section_whitelist)
            records.append(record)
        except ScraperFetchError as exc:
            logger.error("Failed to fetch %s: %s", url, exc)
            failures.append({"url": url, "reason": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to parse %s: %s", url, exc)
            failures.append({"url": url, "reason": str(exc)})

        # Delay between requests, not after the last one
        if i < len(urls) - 1:
            time.sleep(delay)

    return records, failures


@router.post("/scrape", response_model=ScrapeResponse)
async def scrape(request: ScrapeRequest, req: Request) -> ScrapeResponse:
    """Trigger scraping of one or more MoneyControl URLs.

    Runs the synchronous scraper in a thread pool executor, then persists
    results according to the active output_mode in config.yaml.

    Returns:
        200 ScrapeResponse on full or partial success.
        502 if all URLs fail to scrape.
    """
    config = req.app.state.config
    pool = getattr(req.app.state, "pool", None)
    schema = getattr(req.app.state, "schema", None)

    url_strings = [str(u) for u in request.urls]

    # Run synchronous scraper in thread pool to avoid blocking the event loop
    loop = asyncio.get_event_loop()
    records, failures = await loop.run_in_executor(
        None, _run_scraper, url_strings, config.delay, config.sections
    )

    # All URLs failed → 502
    if not records and failures:
        raise HTTPException(
            status_code=502,
            detail={"failures": failures},
        )

    inserted = 0
    skipped = 0

    output_mode = config.output_mode  # already normalised to lowercase

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
        inserted, skipped = await writer.write_batch(records)
    elif output_mode == "file" and records:
        # File-only mode: count all records as "inserted" for response clarity
        inserted = len(records)
        skipped = 0

    return ScrapeResponse(
        processed=len(url_strings),
        inserted=inserted,
        skipped=skipped,
        failures=failures,
    )
