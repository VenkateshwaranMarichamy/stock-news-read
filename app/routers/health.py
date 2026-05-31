"""Health check router — GET /health.

Requirements: 5.2, 5.3, 5.4
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/health")
async def health_check(request: Request) -> dict:
    """On-demand DB ping with 5-second asyncio timeout.

    Returns:
        200 ``{"status": "ok"}`` when the application is running and the DB
        is reachable (or when the pool is not configured).
        200 ``{"status": "ok"}`` when the DB check times out (optimistic —
        a timeout does not confirm failure per Requirement 5.4).
        503 ``{"status": "degraded", "detail": reason}`` when the DB is
        confirmed unreachable (connection refused, DNS failure, auth error).
    """
    pool = getattr(request.app.state, "pool", None)

    # No pool means file-only mode — always healthy
    if pool is None:
        return {"status": "ok"}

    try:
        async with asyncio.timeout(5.0):
            await pool.fetchval("SELECT 1")
        return {"status": "ok"}
    except asyncio.TimeoutError:
        # Optimistic: timeout does not confirm the DB is down (Req 5.4)
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"status": "degraded", "detail": str(exc)},
        )
