"""Data-foundation endpoints.

GET  /api/data/coverage        → the Phase-1 gate report (read-only)
GET  /api/data/jobs            → last ingestion job state
POST /api/data/ingest/daily    → start universe refresh + daily backfill (bearer, background)
POST /api/data/ingest/intraday → start the newest-first 1-minute backfill (bearer, background)
GET  /api/data/coverage/intraday → calendar-aware 1-minute completeness
GET  /api/data/bars            → derived intraday bars (THE shared derivation; read-only)
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.data.coverage import coverage_report
from app.data.intraday import DERIVED_MINUTES, derive_bars
from app.data.intraday_coverage import intraday_report, review_sessions
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


MAX_BARS_SPAN_DAYS = 31


def _guard_start(request: Request) -> JSONResponse | None:
    if not request.app.state.settings.kite_configured:
        return JSONResponse({"started": False, "detail": "Kite not configured"}, status_code=503)
    if request.app.state.kite_sessions.current() is None:
        return JSONResponse(
            {"started": False, "detail": "No valid Kite session — log in first"},
            status_code=409,
        )
    return None


@router.post("/ingest/intraday")
async def start_intraday_ingest(request: Request, symbols: str | None = None) -> JSONResponse:
    blocked = _guard_start(request)
    if blocked is not None:
        return blocked
    runner = _runner(request)
    wanted = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    if not runner.start(lambda: runner.intraday_ingest(wanted)):
        return JSONResponse(
            {"started": False, "detail": "an ingestion job is already running"}, status_code=409
        )
    return JSONResponse({"started": True}, status_code=202)


@router.get("/coverage/intraday")
async def get_intraday_coverage(request: Request, review: str | None = None) -> JSONResponse:
    async with request.app.state.pool.acquire() as conn:
        report = await intraday_report(conn, request.app.state.clock.now())
        if review:
            report["review"] = {"symbol": review, "sessions": await review_sessions(conn, review)}
    return JSONResponse(report)


@router.get("/bars")
async def get_bars(
    request: Request, symbol: str, timeframe: str, start: date, end: date
) -> JSONResponse:
    if timeframe != "1m" and timeframe not in DERIVED_MINUTES:
        return JSONResponse({"detail": f"unsupported timeframe {timeframe!r}"}, status_code=422)
    if end < start or (end - start).days > MAX_BARS_SPAN_DAYS:
        return JSONResponse(
            {"detail": f"start..end must span 0..{MAX_BARS_SPAN_DAYS} days"}, status_code=422
        )
    async with request.app.state.pool.acquire() as conn:
        bars = await derive_bars(conn, symbol, timeframe, start, end)
    return JSONResponse(
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "prices": "integer paisa",
            "bars": [
                {
                    "t": b.bucket.isoformat(),
                    "o": b.open,
                    "h": b.high,
                    "l": b.low,
                    "c": b.close,
                    "v": b.volume,
                    "minutes": b.minutes,
                    "anchored": b.anchored,
                }
                for b in bars
            ],
        }
    )
