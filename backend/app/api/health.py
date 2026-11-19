"""Liveness + dependency health. Honest: reports degraded (503) rather than a fake "ok"."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

router = APIRouter()


async def _db_ok(pool: Any, acquire_timeout_s: float) -> bool:
    try:
        async with pool.acquire(timeout=acquire_timeout_s) as conn:
            return bool(await conn.fetchval("SELECT 1") == 1)
    except Exception:
        log.warning("health: database check failed", exc_info=True)
        return False


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    state = request.app.state
    db_ok = await _db_ok(state.pool, state.settings.db_connect_timeout_seconds)
    scheduler_ok = bool(state.scheduler.running)
    healthy = db_ok and scheduler_ok
    return JSONResponse(
        {
            "status": "ok" if healthy else "degraded",
            "paper_mode": state.settings.paper_mode,
            "db": "ok" if db_ok else "error",
            "scheduler": "running" if scheduler_ok else "stopped",
            "version": request.app.version,
        },
        status_code=200 if healthy else 503,
    )
