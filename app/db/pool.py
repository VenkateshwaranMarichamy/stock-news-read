"""asyncpg connection pool management.

Provides :func:`create_pool` and :func:`close_pool` for managing the
application-level PostgreSQL connection pool.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from moneycontrol_scraper.config import DatabaseConfig

logger = logging.getLogger(__name__)


async def create_pool(db_config: DatabaseConfig) -> asyncpg.Pool:
    """Create and return an asyncpg connection pool.

    Uses ``min_pool_size`` and ``max_pool_size`` from *db_config* to size the
    pool.  Raises a descriptive :class:`RuntimeError` (including the relevant
    parameter name and its configured value) if the connection cannot be
    established.

    Args:
        db_config: Database connection settings from :class:`DatabaseConfig`.

    Returns:
        A ready-to-use :class:`asyncpg.Pool`.

    Raises:
        RuntimeError: When the database connection fails, with a message that
            includes the most relevant parameter name and its configured value.
    """
    try:
        pool = await asyncpg.create_pool(
            host=db_config.host,
            port=db_config.port,
            database=db_config.dbname,
            user=db_config.user,
            password=db_config.password,
            min_size=db_config.min_pool_size,
            max_size=db_config.max_pool_size,
        )
        return pool  # type: ignore[return-value]
    except asyncpg.InvalidCatalogNameError as exc:
        raise RuntimeError(
            f"Database connection failed for dbname={db_config.dbname!r}: {exc}"
        ) from exc
    except asyncpg.InvalidPasswordError as exc:
        raise RuntimeError(
            f"Database connection failed for user={db_config.user!r}: {exc}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Database connection failed for host={db_config.host!r}: {exc}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Database connection failed for host={db_config.host!r}: {exc}"
        ) from exc


async def close_pool(pool: asyncpg.Pool, timeout: float = 30.0) -> None:
    """Close the connection pool gracefully.

    Attempts a graceful shutdown within *timeout* seconds.  If the pool does
    not close in time, all connections are forcibly terminated via
    :meth:`asyncpg.Pool.terminate`.

    Args:
        pool:    The pool to close.
        timeout: Maximum seconds to wait for a graceful close (default 30).
    """
    try:
        await asyncio.wait_for(pool.close(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "Pool did not close within %.1f seconds — forcibly terminating connections.",
            timeout,
        )
        pool.terminate()
