"""Data-foundation endpoints.

GET  /api/data/coverage        → the Phase-1 gate report (read-only)
GET  /api/data/jobs            → last ingestion job state
POST /api/data/ingest/daily    → start universe refresh + daily backfill (bearer, background)
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.data.coverage import coverage_report
from app.data.jobs import IngestRunner

router = APIRouter(prefix="/api/data")


def _runner(request: Request) -> IngestRunner:
    runner: IngestRunner = request.app.state.ingest
    return runner


@router.get("/coverage")
async def get_coverage(request: Request) -> JSONResponse:
    async with request.app.state.pool.acquire() as conn:
        report = await coverage_report(
            conn, request.app.state.settings, request.app.state.clock.now()
        )
    return JSONResponse(report.to_dict())


@router.get("/jobs")
async def get_jobs(request: Request) -> JSONResponse:
    runner = _runner(request)
    return JSONResponse({"busy": runner.busy, "last": runner.last.to_dict()})


@router.post("/ingest/daily")
async def start_daily_ingest(
    request: Request,
    refresh_universe: bool = True,
    symbols: str | None = None,
    reset_cursor: bool = False,
) -> JSONResponse:
    """`symbols` = comma-separated EXCHANGE:TRADINGSYMBOL list to limit the run;
    `reset_cursor=true` (requires `symbols`) re-fetches those symbols' full history."""
    runner = _runner(request)
    wanted = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    if reset_cursor and not wanted:
        return JSONResponse(
            {"started": False, "detail": "reset_cursor requires an explicit symbols list"},
            status_code=422,
        )
    if not request.app.state.settings.kite_configured:
        return JSONResponse({"started": False, "detail": "Kite not configured"}, status_code=503)
    if request.app.state.kite_sessions.current() is None:
        return JSONResponse(
            {"started": False, "detail": "No valid Kite session — log in first"},
            status_code=409,
        )
    started = runner.start(
        lambda: runner.daily_ingest(wanted, refresh=refresh_universe, reset=reset_cursor)
    )
    if not started:
        return JSONResponse(
            {"started": False, "detail": "an ingestion job is already running"}, status_code=409
        )
    return JSONResponse({"started": True}, status_code=202)
