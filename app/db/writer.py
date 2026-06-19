"""DB_Writer: maps OutputRecord objects to news_staging rows."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import asyncpg

from moneycontrol_scraper.models import OutputRecord

logger = logging.getLogger(__name__)


class DB_Writer:
    """Writes scraped OutputRecord objects to the news_staging table."""

    def __init__(self, pool, schema: str) -> None:
        self.pool = pool
        self.schema = schema

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_title(self, record: OutputRecord) -> str:
        """Derive article title from the URL slug.

        Priority order per spec:
          1. og:title  (not available at write time — HTML already parsed)
          2. <title>   (not available at write time — HTML already parsed)
          3. Last non-empty path segment of the URL, hyphens → spaces, trimmed.

        Since the HTML is not re-fetched at write time, only the URL slug
        fallback is implemented here.
        """
        parsed = urlparse(record.url)
        # Split path and take the last non-empty segment
        segments = [seg for seg in parsed.path.split("/") if seg]
        if segments:
            slug = segments[-1]
            return slug.replace("-", " ").strip()
        # Absolute fallback: return the raw URL
        return record.url

    def _extract_stocks(self, sections: dict) -> list[str]:
        """Return the sorted, deduplicated list of non-empty stock name keys.

        Iterates over all section dicts, collects every key, filters out
        empty strings, deduplicates via a set, and returns a sorted list
        for determinism.  Returns [] when sections is empty or yields no
        non-empty keys.
        """
        stock_set: set[str] = set()
        for section_dict in sections.values():
            for key in section_dict:
                if key:  # filter out empty strings
                    stock_set.add(key)
        return sorted(stock_set)

    def _parse_published_date(self, date_str: str | None) -> datetime | None:
        """Parse a YYYY-MM-DD string to a midnight UTC datetime.

        Returns None for None input, empty strings, or any value that
        cannot be parsed as a valid date.
        """
        if date_str is None:
            return None
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------
    # Public mapping
    # ------------------------------------------------------------------

    async def write_batch(self, records: list[OutputRecord]) -> tuple[int, int, dict[str, int]]:
        """
        Insert a batch of OutputRecord objects into news_staging.
        Returns (inserted_count, skipped_count, url_to_staging_id).

        url_to_staging_id maps url → news_staging.id for inserted rows only.
        Skipped/duplicate rows are absent from the map.
        """
        sql = (
            f"INSERT INTO {self.schema}.news_staging"
            " (url, source, title, published_date, content, extracted_stocks, is_loaded)"
            " VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)"
            " ON CONFLICT (url) DO NOTHING"
            " RETURNING id"
        )

        inserted = 0
        skipped = 0
        url_to_staging_id: dict[str, int] = {}

        for record in records:
            row = self.map_record(record)
            url = row["url"]
            source = row["source"]
            title = row["title"]
            published_date = row["published_date"]
            content = row["content"]
            extracted_stocks = row["extracted_stocks"]
            is_loaded = row["is_loaded"]

            last_exc = None
            for attempt in range(3):
                try:
                    async with self.pool.acquire() as conn:
                        async with conn.transaction():
                            result_row = await conn.fetchrow(
                                sql,
                                url,
                                source,
                                title,
                                published_date,
                                content,
                                extracted_stocks,
                                is_loaded,
                            )
                    if result_row is None:
                        # ON CONFLICT DO NOTHING — duplicate
                        logger.info("Skipped duplicate URL: %s", url)
                        skipped += 1
                    else:
                        inserted += 1
                        url_to_staging_id[url] = result_row["id"]
                    last_exc = None
                    break  # success — exit retry loop
                except (asyncpg.PostgresConnectionError, asyncpg.TooManyConnectionsError) as exc:
                    last_exc = exc
                    if attempt < 2:
                        await asyncio.sleep(1)
                except Exception as exc:
                    logger.error("Failed to insert record for URL %s: %s", url, exc)
                    last_exc = None
                    break  # permanent error — move to next record

            if last_exc is not None:
                # All 3 retry attempts exhausted on a transient error
                logger.error("Failed to insert record for URL %s: %s", url, last_exc)

        logger.info("Batch complete: %d inserted, %d skipped.", inserted, skipped)
        return (inserted, skipped, url_to_staging_id)

    def map_record(self, record: OutputRecord) -> dict:
        """Map an OutputRecord to a dict matching the news_staging schema.

        Returns a dict with keys:
            url, source, title, published_date, content,
            extracted_stocks, is_loaded

        Does NOT include ``created_at`` — the column DEFAULT now() handles it.
        ``extracted_stocks`` is serialised to a JSON string so it can be
        cast to JSONB in the INSERT statement.
        """
        stocks = self._extract_stocks(record.sections)
        return {
            "url": record.url,
            "source": "moneycontrol",
            "title": self._extract_title(record),
            "published_date": self._parse_published_date(record.date),
            "content": json.dumps(record.sections),
            "extracted_stocks": json.dumps(stocks),
            "is_loaded": False,
        }
