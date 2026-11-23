"""Admin monitoring and bearer-protected controls for in-process work."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.data import (
    start_adjustment_analysis,
    start_daily_ingest,
    start_fo_history,
    start_intraday_ingest,
    start_nse_archive,
    start_provisional_repair,
    start_reference,
)
from app.core.tradingdate import MARKET_TZ
from app.data.jobs import IngestRunner

router = APIRouter(prefix="/api/admin")

_QUARANTINE_SQL = """
SELECT timeframe, reason, count(*) AS n FROM app.ohlcv_quarantine
GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2
"""
_FLAGS_SQL = """
SELECT timeframe, flag, count(*) AS n FROM app.ohlcv_flags GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2
"""
_CURSOR_ERRORS_SQL = """
SELECT '1d' AS grain, symbol, last_error, updated_at FROM app.ingest_cursor WHERE status = 'error'
UNION ALL
SELECT '1m', i.symbol, c.last_error, c.updated_at
FROM app.intraday_cursor c JOIN app.instrument_ids i USING (sid) WHERE c.status = 'error'
ORDER BY 4 DESC LIMIT 50
"""
_REFERENCE_ERRORS_SQL = """
SELECT dataset, trade_date, detail FROM app.reference_files
WHERE status = 'error' ORDER BY fetched_at DESC LIMIT 50
"""

_START: dict[str, Callable[[Request], Awaitable[JSONResponse]]] = {
    "daily_ingest": start_daily_ingest,
    "intraday_ingest": start_intraday_ingest,
    "nse_archive_ingest": start_nse_archive,
    "reference_ingest": start_reference,
    "fo_history_ingest": start_fo_history,
    "adjustment_analysis": start_adjustment_analysis,
    "repair_provisional": start_provisional_repair,
}


def _scheduler(request: Request) -> dict[str, Any] | None:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        return None
    return {
        "running": bool(scheduler.running),
        "jobs": [
            {
                "id": job.id,
                "next_run_at": job.next_run_time and job.next_run_time.isoformat(),
                "trigger": str(job.trigger),
            }
            for job in sorted(scheduler.get_jobs(), key=lambda j: j.id)
        ],
    }


@router.post("/jobs/{name}/{action}")
async def control_job(
    request: Request,
    name: str,
    action: Literal["start", "stop", "pause", "resume"],
    started_at: datetime | None = None,
) -> JSONResponse:
    runner: IngestRunner = request.app.state.ingest
    if action == "stop":
        if started_at is None:
            return JSONResponse({"detail": "The selected run's start time is required"}, 422)
        if not runner.stop(name, started_at):
            return JSONResponse({"detail": "The selected run is no longer stoppable"}, 409)
        return JSONResponse({"accepted": True}, 202)

    if action == "start":
        start = _START.get(name)
        if start is not None:
            return await start(request)
        service = request.app.state.job_services.get(name)
        if service is None:
            return JSONResponse({"detail": "This service is unavailable"}, 404)
        if name == "ticks_capture":
            now = request.app.state.clock.now()
            start_at, end_at = request.app.state.capture.window(now)
            if now.astimezone(MARKET_TZ).weekday() >= 5 or not start_at <= now < end_at:
                return JSONResponse({"detail": "Outside the live tick capture window"}, 409)
        elif name == "option_chain":
            if not request.app.state.chain.in_window(request.app.state.clock.now()):
                return JSONResponse({"detail": "Outside the option-chain capture window"}, 409)
        if (
            name in ("ticks_capture", "option_chain")
            and request.app.state.kite_sessions.current() is None
        ):
            return JSONResponse({"detail": "No valid Kite session — log in first"}, 409)
        if not runner.start_service(name, service):
            return JSONResponse({"detail": "This job is already running"}, 409)
        return JSONResponse({"accepted": True}, 202)

    scheduler = request.app.state.scheduler
    job = scheduler.get_job(name)
    if job is None:
        return JSONResponse({"detail": "This job has no schedule"}, 404)
    if action == "pause" and job.next_run_time is not None:
        scheduler.pause_job(name)
    elif action == "resume" and job.next_run_time is None:
        scheduler.resume_job(name)
    return JSONResponse({"accepted": True})


@router.get("/overview")
async def overview(request: Request) -> JSONResponse:
    runner: IngestRunner = request.app.state.ingest
    async with request.app.state.pool.acquire() as conn:
        quarantine = await conn.fetch(_QUARANTINE_SQL)
        flags = await conn.fetch(_FLAGS_SQL)
        cursor_errors = await conn.fetch(_CURSOR_ERRORS_SQL)
        reference_errors = await conn.fetch(_REFERENCE_ERRORS_SQL)
    return JSONResponse(
        {
            "generated_at": request.app.state.clock.now().isoformat(),
            "history_since": request.app.state.started_at.isoformat(),
            "job_running": runner.active,
            "operable_jobs": list(_START) + list(request.app.state.job_services),
            "scheduler": _scheduler(request),
            "runs": [state.to_dict() for state in runner.history],
            "reference_errors": [
                {"dataset": r["dataset"], "date": r["trade_date"].isoformat(), "error": r["detail"]}
                for r in reference_errors
            ],
            "quarantine": [
                {"timeframe": r["timeframe"], "reason": r["reason"], "count": r["n"]}
                for r in quarantine
            ],
            "flags": [
                {"timeframe": r["timeframe"], "flag": r["flag"], "count": r["n"]} for r in flags
            ],
            "cursor_errors": [
                {
                    "grain": r["grain"],
                    "symbol": r["symbol"],
                    "error": r["last_error"],
                    "updated_at": r["updated_at"].isoformat(),
                }
                for r in cursor_errors
            ],
        }
    )
