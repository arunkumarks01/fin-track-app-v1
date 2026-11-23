"""Data-foundation endpoints.

GET  /api/data/coverage        → the Phase-1 gate report (read-only)
GET  /api/data/jobs            → last ingestion job state
POST /api/data/ingest/daily    → start universe refresh + daily backfill (bearer, background)
POST /api/data/ingest/intraday → start the newest-first 1-minute backfill (bearer, background)
GET  /api/data/coverage/intraday → calendar-aware 1-minute completeness
GET  /api/data/bars            → derived intraday bars (THE shared derivation; read-only)
POST /api/data/ingest/nse-archive → NSE archive load + adjustment analysis (bearer)
POST /api/data/ingest/reference  → NSE delivery % + participant positions (bearer)
POST /api/data/adjustments/analyse → re-measure Kite's adjustment (bearer)
POST /api/data/repair/provisional → audited repair of provisional 1d bars (bearer)
GET  /api/data/adjustments[?symbol] → measured adjustment summary / one symbol's windows
GET  /api/data/as-traded       → official as-traded 1d (NSE) or derived 1m bars, with quality
GET  /api/data/repairs         → audited repairs + acceptance vs NSE official closes
GET  /api/data/symbols         → the universe (chart symbol search)
GET  /api/data/stock?symbol    → one stock: names, NSE actions vs Kite, data health
GET  /api/data/reference[?days] → 1e ledger + FII/DII/Pro/Client derivatives positions
POST /api/data/ingest/news | ingest/fred → fetch now (bearer)
GET  /api/data/news, /macro    → recent headlines + feed status; FRED series + status
POST /api/data/calendar/import → reviewed economic-calendar CSV, all-or-nothing (bearer)
GET  /api/data/calendar[?days] → upcoming economic events with their source links
GET  /api/data/ticks           → 1d: tick capture status, Parquet export ledger, chain snapshots
GET  /api/data/chain?underlying → the newest raw option-chain snapshot of one underlying
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
from app.data.econ_calendar import (
    CalendarImportError,
    import_calendar,
    parse_calendar_csv,
    upcoming,
)
from app.data.fo_report import fo_summary
from app.data.instruments import GIFT_NIFTY_SYMBOL
from app.data.intraday import DERIVED_MINUTES, derive_bars
from app.data.intraday_coverage import intraday_report, review_sessions
from app.data.jobs import IngestRunner
from app.data.nse_load import reset_archive_day
from app.data.nse_reference import DATASETS
from app.data.reference_report import (
    macro_latest,
    participant_positions,
    recent_news,
    reference_counts,
    reference_ledger,
)
from app.data.stock import stock_profile
from app.data.tick_report import latest_chain, ticks_status

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
    if symbol == GIFT_NIFTY_SYMBOL and timeframe != "1m":
        return JSONResponse(
            {"detail": "GIFT Nifty is available only at its native 1-minute interval here"},
            status_code=422,
        )
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


@router.post("/ingest/reference")
async def start_reference(
    request: Request, datasets: str | None = None, retry_missing: bool = False
) -> JSONResponse:
    runner = _runner(request)
    wanted = [d.strip() for d in datasets.split(",") if d.strip()] if datasets else None
    unknown = [d for d in wanted or [] if d not in DATASETS]
    if unknown:
        return JSONResponse({"detail": f"unknown dataset(s): {unknown}"}, status_code=422)
    if not runner.start(lambda: runner.reference_ingest(wanted, retry_missing)):
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


@router.get("/reference")
async def get_reference(request: Request, days: int = 20) -> JSONResponse:
    """Phase 1e load ledger + the latest `days` sessions of FII/DII/Pro/Client positions."""
    if not 1 <= days <= 250:
        return JSONResponse({"detail": "days must be 1..250"}, status_code=422)
    today = request.app.state.clock.now().astimezone(MARKET_TZ).date()
    async with request.app.state.pool.acquire() as conn:
        return JSONResponse(
            {
                "ledger": await reference_ledger(conn),
                "participants": await participant_positions(conn, days),
                "sources": {
                    **await reference_counts(conn, today),
                    "fred_key_configured": request.app.state.settings.fred_api_key is not None,
                },
            }
        )


@router.post("/ingest/fo-history")
async def start_fo_history(request: Request, retry_missing: bool = False) -> JSONResponse:
    runner = _runner(request)
    if not runner.start(lambda: runner.fo_history_ingest(retry_missing)):
        return _busy()
    return JSONResponse({"started": True}, status_code=202)


@router.get("/fo")
async def get_fo(request: Request) -> JSONResponse:
    """F&O history coverage: ledger by status, stored range, rows, and the biggest underlyings."""
    async with request.app.state.pool.acquire() as conn:
        return JSONResponse(await fo_summary(conn))


@router.get("/ticks")
async def get_ticks(request: Request) -> JSONResponse:
    """Slice 1d status: live capture, recent runs, Parquet export ledger, chain snapshots."""
    cap = (
        getattr(request.app.state, "capture", None)
        if request.app.state.settings.kite_configured
        else None
    )
    async with request.app.state.pool.acquire() as conn:
        return JSONResponse(
            await ticks_status(conn, cap.stats.to_dict() if cap is not None else None)
        )


@router.get("/chain")
async def get_chain(request: Request, underlying: str) -> JSONResponse:
    async with request.app.state.pool.acquire() as conn:
        chain = await latest_chain(conn, underlying.upper())
    if chain is None:
        return JSONResponse({"detail": f"no snapshot stored for {underlying}"}, status_code=404)
    return JSONResponse(chain)


@router.post("/ingest/news")
async def start_news(request: Request) -> JSONResponse:
    state = await _runner(request).news_fetch()
    return JSONResponse(state.to_dict(), status_code=200 if state.status == "ok" else 503)


@router.post("/ingest/fred")
async def start_fred(request: Request) -> JSONResponse:
    state = await _runner(request).fred_fetch()
    return JSONResponse(state.to_dict(), status_code=200 if state.status == "ok" else 503)


@router.get("/news")
async def get_news(request: Request, limit: int = 50) -> JSONResponse:
    if not 1 <= limit <= 500:
        return JSONResponse({"detail": "limit must be 1..500"}, status_code=422)
    async with request.app.state.pool.acquire() as conn:
        return JSONResponse(await recent_news(conn, limit))


@router.get("/macro")
async def get_macro(request: Request) -> JSONResponse:
    async with request.app.state.pool.acquire() as conn:
        return JSONResponse(
            {
                "key_configured": request.app.state.settings.fred_api_key is not None,
                "series": await macro_latest(conn),
            }
        )


@router.post("/calendar/import")
async def post_calendar(request: Request) -> JSONResponse:
    """Body: the reviewed CSV (see app/data/econ_calendar.py). All-or-nothing."""
    try:
        text = (await request.body()).decode("utf-8-sig")
        events = parse_calendar_csv(text)
    except UnicodeDecodeError:
        return JSONResponse({"imported": 0, "problems": ["CSV must be UTF-8"]}, status_code=422)
    except CalendarImportError as exc:
        return JSONResponse({"imported": 0, "problems": exc.problems}, status_code=422)
    async with request.app.state.pool.acquire() as conn, conn.transaction():
        new = await import_calendar(conn, events, request.app.state.clock.now())
    return JSONResponse({"rows": len(events), "imported": new})


@router.get("/calendar")
async def get_calendar(request: Request, days: int = 30) -> JSONResponse:
    if not 1 <= days <= 400:
        return JSONResponse({"detail": "days must be 1..400"}, status_code=422)
    today = request.app.state.clock.now().astimezone(MARKET_TZ).date()
    async with request.app.state.pool.acquire() as conn:
        return JSONResponse(
            {
                "from": today.isoformat(),
                "events": await upcoming(conn, today, today + timedelta(days=days)),
            }
        )


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
