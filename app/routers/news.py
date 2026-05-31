"""
News read endpoints.

Covers requirements: 7.1–7.9, 8.1–8.8
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request

from app.db.queries import fetch_news_by_id, fetch_news_by_url, fetch_news_list
from app.schemas import NewsListResponse, NewsRecordSchema

router = APIRouter()


@router.get("/news", response_model=NewsListResponse)
async def list_news(
    request: Request,
    date: str | None = Query(default=None, description="Filter by date YYYY-MM-DD"),
    stock: str | None = Query(default=None),
    source: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> NewsListResponse:
    if date is not None:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=422, detail="date must be in YYYY-MM-DD format")

    pool = request.app.state.pool
    schema = request.app.state.schema
    items, total = await fetch_news_list(
        pool, schema,
        date=date, stock=stock, source=source,
        page=page, page_size=page_size,
    )
    return NewsListResponse(items=items, total=total, page=page, page_size=page_size)


# IMPORTANT: /news/by-url MUST be registered before /news/{id}
# to prevent FastAPI from treating "by-url" as a path parameter value.
@router.get("/news/by-url", response_model=NewsRecordSchema)
async def get_news_by_url(
    request: Request,
    url: str = Query(..., description="Exact URL to look up"),
) -> NewsRecordSchema:
    pool = request.app.state.pool
    schema = request.app.state.schema
    record = await fetch_news_by_url(pool, schema, url)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


@router.get("/news/{id}", response_model=NewsRecordSchema)
async def get_news_by_id(
    id: int,
    request: Request,
) -> NewsRecordSchema:
    pool = request.app.state.pool
    schema = request.app.state.schema
    record = await fetch_news_by_id(pool, schema, id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return record
