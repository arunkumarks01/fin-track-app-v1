"""Data-foundation endpoints.

GET  /api/data/coverage        → the Phase-1 gate report (read-only)
GET  /api/data/jobs            → last ingestion job state
POST /api/data/ingest/daily    → start universe refresh + daily backfill (bearer, background)
POST /api/data/ingest/intraday → start the newest-first 1-minute backfill (bearer, background)
GET  /api/data/coverage/intraday → calendar-aware 1-minute completeness
GET  /api/data/bars            → derived intraday bars (THE shared derivation; read-only)
POST /api/data/ingest/nse-archive → NSE archive load + adjustment analysis (bearer)
POST /api/data/adjustments/analyse → re-measure Kite's adjustment (bearer)
POST /api/data/repair/provisional → audited repair of provisional 1d bars (bearer)
GET  /api/data/adjustments[?symbol] → measured adjustment summary / one symbol's windows
GET  /api/data/as-traded       → official as-traded 1d (NSE) or derived 1m bars, with quality
GET  /api/data/repairs         → audited repairs + acceptance vs NSE official closes
GET  /api/data/symbols         → the universe (chart symbol search)
GET  /api/data/stock?symbol    → one stock: names, NSE actions vs Kite, data health
GET  /api/data/candles         → paged chart candles per interval, adjusted or as-traded
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.tradingdate import MARKET_TZ
from app.data.adjustment_report import adjustments_summary, repairs_report, symbol_adjustments
from app.data.candles import CandleRequestError, UnknownSymbol, candles, symbols
from app.data.coverage import coverage_report
from app.data.intraday import DERIVED_MINUTES, derive_bars
from app.data.intraday_coverage import intraday_report, review_sessions
from app.data.jobs import IngestRunner
from app.data.nse_load import reset_archive_day
from app.data.stock import stock_profile

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
        report = await intraday_report(
            conn,
            request.app.state.clock.now(),
            request.app.state.settings.intraday_analysis_start,
        )
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


def _busy() -> JSONResponse:
    return JSONResponse(
        {"started": False, "detail": "an ingestion job is already running"}, status_code=409
    )


@router.post("/ingest/nse-archive")
async def start_nse_archive(request: Request, retry_missing: bool = False) -> JSONResponse:
    runner = _runner(request)
    if not runner.start(lambda: runner.nse_archive_ingest(retry_missing)):
        return _busy()
    return JSONResponse({"started": True}, status_code=202)


@router.post("/adjustments/analyse")
async def start_adjustment_analysis(request: Request, symbols: str | None = None) -> JSONResponse:
    runner = _runner(request)
    wanted = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    if not runner.start(lambda: runner.adjustment_analysis(wanted)):
        return _busy()
    return JSONResponse({"started": True}, status_code=202)


@router.post("/repair/provisional")
async def start_provisional_repair(request: Request) -> JSONResponse:
    blocked = _guard_start(request)
    if blocked is not None:
        return blocked
    runner = _runner(request)
    if not runner.start(runner.repair_provisional):
        return _busy()
    return JSONResponse({"started": True}, status_code=202)


@router.get("/adjustments")
async def get_adjustments(request: Request, symbol: str | None = None) -> JSONResponse:
    async with request.app.state.pool.acquire() as conn:
        if symbol:
            return JSONResponse(await symbol_adjustments(conn, symbol))
        return JSONResponse(
            await adjustments_summary(
                conn, request.app.state.settings, request.app.state.clock.now()
            )
        )


@router.get("/repairs")
async def get_repairs(request: Request) -> JSONResponse:
    async with request.app.state.pool.acquire() as conn:
        return JSONResponse(await repairs_report(conn))


@router.get("/as-traded")
async def get_as_traded(
    request: Request, symbol: str, grain: str, start: date, end: date
) -> JSONResponse:
    """Official as-traded prices: 1d straight from NSE's bhavcopy (exact); 1m derived as
    stored ÷ measured factor, NULL (never guessed) where the day isn't usable."""
    if grain not in ("1d", "1m"):
        return JSONResponse({"detail": "grain must be 1d or 1m"}, status_code=422)
    limit = MAX_BARS_SPAN_DAYS if grain == "1m" else 3700
    if end < start or (end - start).days > limit:
        return JSONResponse({"detail": f"start..end must span 0..{limit} days"}, status_code=422)
    async with request.app.state.pool.acquire() as conn:
        if grain == "1d":
            rows = await conn.fetch(
                """SELECT trade_date, open, high, low, close, volume FROM app.nse_bhav
                   WHERE symbol = $1 AND trade_date BETWEEN $2 AND $3 ORDER BY 1""",
                symbol,
                start,
                end,
            )
            bars = [
                {
                    "t": r["trade_date"].isoformat(),
                    "o": r["open"],
                    "h": r["high"],
                    "l": r["low"],
                    "c": r["close"],
                    "v": r["volume"],
                    "quality": "official",
                }
                for r in rows
            ]
        else:
            lo = datetime.combine(start, time.min, MARKET_TZ)
            hi = datetime.combine(end + timedelta(days=1), time.min, MARKET_TZ)
            rows = await conn.fetch(
                "SELECT * FROM app.minute_as_traded($1, $2, $3)", symbol, lo, hi
            )
            bars = [
                {
                    "t": r["ts"].isoformat(),
                    "o": r["open"],
                    "h": r["high"],
                    "l": r["low"],
                    "c": r["close"],
                    "v": r["volume"],
                    "quality": r["quality"],
                }
                for r in rows
            ]
    return JSONResponse({"symbol": symbol, "grain": grain, "prices": "integer paisa", "bars": bars})


@router.post("/nse-archive/reset-day")
async def reset_nse_archive_day(
    request: Request, day: date, reason: str, purge: bool = False
) -> JSONResponse:
    """Queue one archive day for a single re-fetch; `purge=true` deletes its stored official
    rows first, each logged to app.data_repairs (audited). Bearer-protected."""
    if not reason.strip():
        return JSONResponse({"detail": "a reason is required (it is logged)"}, status_code=422)
    if _runner(request).busy:
        return _busy()
    async with request.app.state.pool.acquire() as conn, conn.transaction():
        deleted = await reset_archive_day(
            conn, day, purge, reason.strip(), request.app.state.clock.now()
        )
    return JSONResponse({"day": day.isoformat(), "purged_rows": deleted, "queued": True})


@router.get("/symbols")
async def get_symbols(request: Request) -> JSONResponse:
    async with request.app.state.pool.acquire() as conn:
        return JSONResponse({"symbols": await symbols(conn)})


@router.get("/stock")
async def get_stock(request: Request, symbol: str) -> JSONResponse:
    """Everything stored about one universe member (read-only): names, NSE actions and how
    Kite's measured prices relate to them, daily + 1m data health."""
    async with request.app.state.pool.acquire() as conn:
        profile = await stock_profile(
            conn, request.app.state.settings, request.app.state.clock.now(), symbol
        )
    if profile is None:
        return JSONResponse({"detail": f"{symbol} is not in the universe"}, status_code=404)
    return JSONResponse(profile)


@router.get("/candles")
async def get_candles(
    request: Request,
    symbol: str,
    interval: str,
    adjustment: str = "adjusted",
    before: int | None = None,
    count: int = 1000,
) -> JSONResponse:
    """Paged backwards from `before` (UTC seconds, exclusive). Read-only."""
    from datetime import UTC

    anchor = datetime.fromtimestamp(before, UTC) if before is not None else None
    try:
        async with request.app.state.pool.acquire() as conn:
            body = await candles(
                conn,
                symbol,
                interval,
                adjustment,
                anchor,
                count,
                request.app.state.settings.chart_max_bars,
            )
    except CandleRequestError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    except UnknownSymbol:
        return JSONResponse({"detail": f"{symbol} is not in the universe"}, status_code=404)
    return JSONResponse(body)
