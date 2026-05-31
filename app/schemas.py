"""
Pydantic v2 schemas for the news-storage-api.

Covers requirements: 6.1, 6.3, 9.1, 9.2, 9.3, 9.4
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator


class NewsRecordSchema(BaseModel):
    """
    Represents a single row from the news_staging table.

    Requirements 9.1, 9.2, 9.3, 9.4:
    - Exactly nine fields, no extras.
    - published_date and created_at are timezone-aware datetimes (UTC offset).
    - content is a parsed JSON object or null.
    - extracted_stocks is always a list of strings, never null.
    """

    id: int
    url: str
    source: str
    title: str
    published_date: datetime | None  # ISO 8601 with UTC offset, or null
    content: dict | None             # parsed JSON object, or null
    extracted_stocks: list[str]      # never null; [] when empty
    created_at: datetime             # ISO 8601 with UTC offset
    is_loaded: bool

    model_config = ConfigDict(from_attributes=True)


class ScrapeRequest(BaseModel):
    """
    Request body for POST /scrape.

    Requirement 6.1: accepts 1–50 MoneyControl article URLs.
    Requirement 6.4: empty list → validation error (HTTP 422).
    Requirement 6.5: non-HTTP/HTTPS URL → validation error (HTTP 422).
    """

    urls: list[HttpUrl]

    @field_validator("urls", mode="after")
    @classmethod
    def validate_url_count(cls, v: list[HttpUrl]) -> list[HttpUrl]:
        if len(v) == 0:
            raise ValueError("At least one URL is required.")
        if len(v) > 50:
            raise ValueError("At most 50 URLs are allowed per request.")
        return v


class ScrapeResponse(BaseModel):
    """
    Response body for POST /scrape.

    Requirement 6.3: includes processed, inserted, skipped counts and a
    failures list of {"url": ..., "reason": ...} dicts.
    """

    processed: int
    inserted: int
    skipped: int
    failures: list[dict]  # each entry: {"url": str, "reason": str}


class NewsListResponse(BaseModel):
    """
    Response body for GET /news.

    Requirement 7.6: items, total, page, page_size.
    """

    items: list[NewsRecordSchema]
    total: int
    page: int
    page_size: int
