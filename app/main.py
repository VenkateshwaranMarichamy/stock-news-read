"""FastAPI application entry point for the news-storage-api.

Start with:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8005
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.db.pool import close_pool, create_pool
from app.routers import health, news, scrape
from moneycontrol_scraper.config import load_config

# Load config once at module level — shared by all routers via app.state
config = load_config()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifespan: create DB pool on startup, close on shutdown."""
    # Store config on app.state so routers can access it without re-loading
    app.state.config = config

    # Create the connection pool only when a database output mode is active
    if config.output_mode in {"database", "both"} and config.database is not None:
        app.state.pool = await create_pool(config.database)
        app.state.schema = config.database.schema
    else:
        app.state.pool = None
        app.state.schema = None

    yield

    # Shutdown: close pool gracefully (30s timeout enforced inside close_pool)
    if app.state.pool is not None:
        await close_pool(app.state.pool)


app = FastAPI(
    title="News Storage API",
    description="API for scraping and querying MoneyControl stock news",
    version="1.0.0",
    lifespan=lifespan,
)

# Register routers — /news/by-url is registered before /news/{id} inside news.py
app.include_router(health.router)
app.include_router(scrape.router)
app.include_router(news.router)
