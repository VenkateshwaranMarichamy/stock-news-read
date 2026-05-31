"""
Read query functions for the news endpoints.

All functions use asyncpg connection pools and return plain dicts
(asyncpg Record objects are converted via dict(row)).
"""

import json
from datetime import datetime, timedelta, timezone

import asyncpg


def _parse_row(row: asyncpg.Record) -> dict:
    """Convert an asyncpg Record to a plain dict, parsing content JSON."""
    d = dict(row)
    if d.get("content") is not None:
        try:
            d["content"] = json.loads(d["content"])
        except (json.JSONDecodeError, TypeError):
            d["content"] = None
    return d


async def fetch_news_list(
    pool: asyncpg.Pool,
    schema: str,
    *,
    date: str | None = None,
    stock: str | None = None,
    source: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """
    Return a paginated list of news records and the total matching count.

    Filters:
    - date: YYYY-MM-DD — matches records whose published_date falls on that
      calendar day in UTC (half-open interval [midnight, midnight+1day)).
    - stock: case-insensitive JSONB containment on extracted_stocks.
    - source: case-insensitive exact match on the source column.

    Results are ordered by created_at DESC.
    Returns (items, total).
    """
    conditions: list[str] = []
    params: list = []

    if date is not None:
        start_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(days=1)
        idx = len(params) + 1
        conditions.append(
            f"published_date >= ${idx}::timestamptz AND published_date < ${idx + 1}::timestamptz"
        )
        params.append(start_dt)
        params.append(end_dt)

    if stock is not None:
        idx = len(params) + 1
        conditions.append(
            f"extracted_stocks @> to_jsonb(lower(${idx})::text)"
        )
        params.append(stock)

    if source is not None:
        idx = len(params) + 1
        conditions.append(f"LOWER(source) = LOWER(${idx})")
        params.append(source)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    offset = (page - 1) * page_size
    # Append pagination params after the filter params
    limit_idx = len(params) + 1
    offset_idx = len(params) + 2

    data_query = (
        f"SELECT * FROM {schema}.news_staging "
        f"{where_clause} "
        f"ORDER BY created_at DESC "
        f"LIMIT ${limit_idx} OFFSET ${offset_idx}"
    )
    count_query = f"SELECT COUNT(*) FROM {schema}.news_staging {where_clause}"

    data_params = params + [page_size, offset]
    count_params = params

    async with pool.acquire() as conn:
        rows = await conn.fetch(data_query, *data_params)
        total = await conn.fetchval(count_query, *count_params)

    items = [_parse_row(row) for row in rows]
    return items, int(total)


async def fetch_news_by_id(
    pool: asyncpg.Pool, schema: str, record_id: int
) -> dict | None:
    """
    Return the news record with the given id, or None if not found.
    The content field is parsed from a JSON string to a dict.
    """
    query = f"SELECT * FROM {schema}.news_staging WHERE id = $1"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, record_id)
    if row is None:
        return None
    return _parse_row(row)


async def fetch_news_by_url(
    pool: asyncpg.Pool, schema: str, url: str
) -> dict | None:
    """
    Return the news record whose url matches exactly, or None if not found.
    The content field is parsed from a JSON string to a dict.
    """
    query = f"SELECT * FROM {schema}.news_staging WHERE url = $1"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, url)
    if row is None:
        return None
    return _parse_row(row)
