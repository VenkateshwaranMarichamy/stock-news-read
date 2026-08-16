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
    Requirement 7.3: events_queued is the total stock entries submitted to pipeline.
    """

    processed: int
    inserted: int
    skipped: int
    failures: list[dict]  # each entry: {"url": str, "reason": str}
    events_queued: int = 0  # total stock entries submitted to pipeline; 0 for file mode
    staging_ids: list[int] = []  # news_staging IDs for each processed record (use with POST /classify)


class NewsListResponse(BaseModel):
    """
    Response body for GET /news.

    Requirement 7.6: items, total, page, page_size.
    """

    items: list[NewsRecordSchema]
    total: int
    page: int
    page_size: int


class ClassifyRequest(BaseModel):
    """
    Request body for POST /classify.

    Requirements 1.1, 1.2, 1.3, 1.4:
    - ids is a list of positive integers.
    - At least one ID is required (non-empty list).
    - At most 50 IDs are allowed per request.
    - All IDs must be positive integers (> 0).
    """

    ids: list[int]

    @field_validator("ids", mode="after")
    @classmethod
    def validate_ids(cls, v: list[int]) -> list[int]:
        if len(v) == 0:
            raise ValueError("At least one ID is required.")
        if len(v) > 50:
            raise ValueError("At most 50 IDs are allowed per request.")
        for val in v:
            if val <= 0:
                raise ValueError("All IDs must be positive integers.")
        return v


class ClassifyResponse(BaseModel):
    """
    Response body for POST /classify.

    Requirements 6.1, 6.2:
    - ids_processed: IDs found in news_staging with at least one stock entry.
    - ids_missing: IDs not found in news_staging.
    - ids_skipped: IDs with null/empty content, or pipeline/start_batch failure.
    - total_stocks: sum of PipelineSummary.total across all runs.
    - events_inserted: sum of PipelineSummary.db_inserted.
    - events_failed: sum of PipelineSummary.db_failures.
    - unresolved_stocks: sum of PipelineSummary.unresolved.
    """

    ids_processed: list[int] = []
    ids_missing: list[int] = []
    ids_skipped: list[int] = []
    total_stocks: int = 0
    events_inserted: int = 0
    events_failed: int = 0
    unresolved_stocks: int = 0
