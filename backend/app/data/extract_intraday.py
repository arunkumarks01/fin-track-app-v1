"""Newest-first, round-robin, resumable 1-minute extractor with a hard disk budget (Phase 1b).

Order: a forward catch-up for symbols that already have data, then backfill ROUNDS — each
round fetches every symbol's next-older Kite window (60 days for `minute`). If the budget or
a crash stops the run, every symbol keeps its most recent history, not just the first N.

Per window, one transaction: new bars (COPY) + quarantine + per-session counts + cursor.
Honesty rules (shared with the daily extractor):
- only completed sessions are fetched (INTRADAY_FINAL_AFTER_IST);
- a re-fetched minute that differs from the stored one is quarantined, never overwritten;
- KiteAuthError stops the whole run; other Kite errors mark that symbol 'error' and skip it
  for the rest of the run;
- the database-size budget is checked before every round and every N windows; reaching it
  stops the run (fail closed) with the cursor consistent.
A symbol reaches its floor at INTRADAY_FLOOR_DATE or after INTRADAY_EMPTY_WINDOWS_STOP
consecutive empty windows (pre-listing history doesn't exist — this also avoids reaching
back into the 'ghost' history of an older security on a reused token).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from app.broker.kite.client import KiteAuthError, KiteError
from app.core.clock import Clock
from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ, trading_date_of
from app.data.drift import proportional_ratio, record_check, record_drift
from app.data.extract_daily import (
    HistoricalSource,
    last_completed_session,
    last_final_daily_session,
)
from app.data.instruments import GIFT_NIFTY_SYMBOL, historical_members
from app.data.intraday import TIMEFRAME, MinuteBar, ensure_sids, validate_minute
from app.data.session_calendar import refresh_session_status
from app.data.validate import Rejected

log = logging.getLogger(__name__)

KITE_INTERVAL = "minute"
SOURCE = "kite:historical:minute"
GiB = 1024**3


@dataclass
class Member:
    symbol: str
    token: int
    sid: int
    newest: datetime | None = None
    oldest: date | None = None
    floored: bool = False
    empty: int = 0
    failed: bool = False


@dataclass
class IntradayReport:
    started_at: datetime
    rounds: int = 0
    windows: int = 0
    inserted: int = 0
    already_stored: int = 0
    quarantined: int = 0
    errors: dict[str, str] = field(default_factory=dict)
    stopped_reason: str | None = None
    db_size_gb: float | None = None
    budget_gb: float = 0.0
    deepest_common_date: date | None = None  # every non-floored symbol reaches back to here

    def totals(self) -> dict[str, Any]:
        return {
            "rounds": self.rounds,
            "windows": self.windows,
            "inserted": self.inserted,
            "already_stored": self.already_stored,
            "quarantined": self.quarantined,
            "errors": len(self.errors),
            "db_size_gb": self.db_size_gb,
            "budget_gb": self.budget_gb,
            "deepest_common_date": self.deepest_common_date
            and self.deepest_common_date.isoformat(),
        }


Progress = Callable[[IntradayReport], None]


class BudgetExceeded(RuntimeError):
    pass


async def db_size_gb(conn: Any) -> float:
    return float(await conn.fetchval("SELECT pg_database_size(current_database())")) / GiB


async def _check_budget(conn: Any, report: IntradayReport, settings: Settings) -> None:
    report.db_size_gb = round(await db_size_gb(conn), 3)
    if report.db_size_gb >= settings.intraday_db_budget_gb:
        raise BudgetExceeded(
            f"disk budget reached: database {report.db_size_gb:.2f} GB ≥ "
            f"{settings.intraday_db_budget_gb:.2f} GB (INTRADAY_DB_BUDGET_GB)"
        )


async def _load_members(
    conn: Any, symbols: list[str] | None, include_reference_indices: bool
) -> list[Member]:
    instruments = await historical_members(conn, symbols, include_reference_indices)
    sids = await ensure_sids(conn, [i.symbol for i in instruments])
    cursors = {
        r["sid"]: r
        for r in await conn.fetch(
            "SELECT * FROM app.intraday_cursor WHERE sid = ANY($1::smallint[])",
            list(sids.values()),
        )
    }
    members = []
    for instrument in instruments:
        sid = sids[instrument.symbol]
        c = cursors.get(sid)
        members.append(
            Member(
                instrument.symbol,
                instrument.instrument_token,
                sid,
                newest=c["newest_ts"] if c else None,
                oldest=c["oldest_date"] if c else None,
                floored=bool(c["floor_reached"]) if c else False,
                empty=int(c["empty_windows"]) if c else 0,
            )
        )
    return members


async def _save_cursor(
    conn: Any, m: Member, status: str, error: str | None, added: int, now: datetime
) -> None:
    await conn.execute(
        """
        INSERT INTO app.intraday_cursor (sid, newest_ts, oldest_date, floor_reached,
            empty_windows, status, last_error, rows_written, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (sid) DO UPDATE SET newest_ts = EXCLUDED.newest_ts,
            oldest_date = EXCLUDED.oldest_date, floor_reached = EXCLUDED.floor_reached,
            empty_windows = EXCLUDED.empty_windows, status = EXCLUDED.status,
            last_error = EXCLUDED.last_error,
            rows_written = app.intraday_cursor.rows_written + EXCLUDED.rows_written,
            updated_at = EXCLUDED.updated_at
        """,
        m.sid,
        m.newest,
        m.oldest,
        m.floored,
        m.empty,
        status,
        error,
        added,
        now,
    )


async def _commit_window(
    conn: Any,
    m: Member,
    records: list[Any],
    w_start: date,
    w_end: date,
    now: datetime,
    check_date: date | None = None,
    tol_paisa: float = 0.0,
) -> tuple[int, int, int, list[MinuteBar]]:
    """Returns (inserted, already_stored, quarantined, clean bars).

    `check_date` is the newest stored session, re-fetched on purpose (drift re-check): if every
    differing minute of it comes back scaled by ONE ratio, Kite re-adjusted history — recorded
    as drift, stored minutes kept as they are; otherwise the differences stay conflicts."""
    v = validate_minute(m.symbol, m.sid, records)
    lo = datetime.combine(w_start, time.min, MARKET_TZ)
    hi = datetime.combine(w_end + timedelta(days=1), time.min, MARKET_TZ)
    existing = (
        {
            r["ts"]: (r["open"], r["high"], r["low"], r["close"], r["volume"])
            for r in await conn.fetch(
                """SELECT ts, open, high, low, close, volume FROM app.ohlcv_1m
               WHERE sid = $1 AND ts >= $2 AND ts < $3""",
                m.sid,
                lo,
                hi,
            )
        }
        if v.clean
        else {}
    )
    fresh: list[MinuteBar] = []
    same = 0
    rejected = list(v.rejected)
    differing: list[tuple[MinuteBar, tuple[int, ...]]] = []
    checked = False
    for b in v.clean:
        stored = existing.get(b.ts)
        if stored is None:
            fresh.append(b)
        elif stored == b.values():
            same += 1
            checked = checked or trading_date_of(b.ts, MARKET_TZ) == check_date
        else:
            differing.append((b, stored))
    on_check_day = bool(differing) and all(
        trading_date_of(b.ts, MARKET_TZ) == check_date for b, _ in differing
    )
    ratio = (
        proportional_ratio([(st, b.values()) for b, st in differing], tol_paisa)
        if on_check_day and check_date is not None
        else None
    )
    if ratio is not None and check_date is not None:
        same += len(differing)
        checked = True
        await record_drift(conn, m.symbol, TIMEFRAME, check_date, ratio, now)
        log.warning("intraday %s: Kite re-adjusted history x%.6f", m.symbol, ratio)
    else:
        rejected.extend(
            Rejected(
                m.symbol,
                TIMEFRAME,
                b.ts,
                "conflicts_with_stored",
                f"stored {stored} vs fetched {b.values()}",
                [b.ts.isoformat(), *b.values()],
            )
            for b, stored in differing
        )
    if checked and check_date is not None:
        await record_check(conn, m.symbol, TIMEFRAME, check_date, now)
    if fresh:
        await conn.copy_records_to_table(
            "ohlcv_1m",
            schema_name="app",
            columns=["ts", "volume", "open", "high", "low", "close", "sid"],
            records=[b.record() for b in fresh],
        )
    if rejected:
        await conn.executemany(
            """INSERT INTO app.ohlcv_quarantine
               (symbol, timeframe, ts, reason, detail, raw, source, quarantined_at)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8) ON CONFLICT DO NOTHING""",
            [
                (
                    r.symbol,
                    r.timeframe,
                    r.ts,
                    r.reason,
                    r.detail[:500],
                    json.dumps(r.raw, default=str),
                    SOURCE,
                    now,
                )
                for r in rejected
            ],
        )
    if fresh:
        await conn.execute(
            """
            INSERT INTO app.intraday_sessions (sid, session_date, bars, first_ts, last_ts,
                                               open_first, high_max, low_min, volume_sum)
            SELECT sid, (ts AT TIME ZONE 'Asia/Kolkata')::date, count(*), min(ts), max(ts),
                   first(open, ts), max(high), min(low), sum(volume)
            FROM app.ohlcv_1m WHERE sid = $1 AND ts >= $2 AND ts < $3
            GROUP BY 1, 2
            ON CONFLICT (sid, session_date) DO UPDATE SET bars = EXCLUDED.bars,
                first_ts = EXCLUDED.first_ts, last_ts = EXCLUDED.last_ts,
                open_first = EXCLUDED.open_first, high_max = EXCLUDED.high_max,
                low_min = EXCLUDED.low_min, volume_sum = EXCLUDED.volume_sum
            """,
            m.sid,
            lo,
            hi,
        )
    return len(fresh), same, len(rejected), v.clean


async def refresh_exchange_sessions(conn: Any, settings: Settings, now: datetime) -> int:
    """Rebuild the derived calendar: open = earliest first minute on the exchange that day;
    expected = the benchmark's minute count (NULL when the benchmark has no data)."""
    rows = 0
    for exchange, benchmark in settings.intraday_benchmarks.items():
        result = await conn.execute(
            """
            INSERT INTO app.exchange_sessions
                (exchange, session_date, session_open, expected_minutes, benchmark, refreshed_at)
            SELECT $1, s.session_date, min(s.first_ts),
                   max(s.bars) FILTER (WHERE i.symbol = $2), $2, $3
            FROM app.intraday_sessions s JOIN app.instrument_ids i USING (sid)
            WHERE split_part(i.symbol, ':', 1) = $1
            GROUP BY s.session_date
            ON CONFLICT (exchange, session_date) DO UPDATE SET
                session_open = EXCLUDED.session_open,
                expected_minutes = EXCLUDED.expected_minutes,
                benchmark = EXCLUDED.benchmark, refreshed_at = EXCLUDED.refreshed_at
            """,
            exchange,
            benchmark,
            now,
        )
        rows += int(result.split()[-1])
    return rows


async def _fetch(source: HistoricalSource, m: Member, start: date, end: date) -> list[Any]:
    return await source.historical_raw(
        m.token,
        KITE_INTERVAL,
        datetime.combine(start, time(0, 0), MARKET_TZ),
        datetime.combine(end, time(23, 59, 59), MARKET_TZ),
    )


async def run_intraday_backfill(
    pool: Any,
    source: HistoricalSource,
    settings: Settings,
    clock: Clock,
    symbols: list[str] | None = None,
    progress: Progress | None = None,
    *,
    include_reference_indices: bool = False,
) -> IntradayReport:
    report = IntradayReport(started_at=clock.now(), budget_gb=settings.intraday_db_budget_gb)
    window_days = settings.kite_window_days[KITE_INTERVAL]
    now = clock.now()
    end = last_completed_session(now, settings.intraday_final_after_ist)
    gift_end = last_final_daily_session(now, settings.daily_bar_final_next_day_ist)
    floor = settings.intraday_floor_date

    async with pool.acquire() as conn:
        members = await _load_members(conn, symbols, include_reference_indices)
        if not members:
            report.stopped_reason = "universe is empty — refresh the universe first"
            return report

    def emit() -> None:
        live = [m.oldest for m in members if not m.floored and m.oldest]
        report.deepest_common_date = max(live) if live else None
        if progress:
            progress(report)

    def final_day(m: Member) -> date:
        # GIFT Nifty trades after the NSE cash close. Catch up only after overnight finality.
        return gift_end if m.symbol == GIFT_NIFTY_SYMBOL else end

    async def window(
        m: Member, w_start: date, w_end: date, backward: bool, check: date | None = None
    ) -> None:
        records = await _fetch(source, m, w_start, w_end)
        now = clock.now()
        async with pool.acquire() as conn, conn.transaction():
            ins, same, quar, clean = await _commit_window(
                conn, m, records, w_start, w_end, now, check, settings.adj_tolerance_paisa
            )
            if clean:
                m.newest = max(m.newest or clean[-1].ts, clean[-1].ts)
            if backward:
                m.oldest = w_start
                m.empty = 0 if clean else m.empty + 1
                if w_start <= floor or m.empty >= settings.intraday_empty_windows_stop:
                    m.floored = True
            await _save_cursor(conn, m, "ok", None, ins, now)
        report.windows += 1
        report.inserted += ins
        report.already_stored += same
        report.quarantined += quar
        if report.windows % settings.intraday_budget_check_every == 0:
            async with pool.acquire() as conn:
                await _check_budget(conn, report, settings)
        emit()

    stop: list[BaseException] = []  # first run-stopping error (auth / budget) halts all workers
    sem = asyncio.Semaphore(settings.intraday_concurrency)

    async def guarded(
        m: Member, w_start: date, w_end: date, backward: bool, check: date | None = None
    ) -> None:
        if stop:
            return
        try:
            async with sem:
                if stop:
                    return
                await window(m, w_start, w_end, backward, check)
        except (KiteAuthError, BudgetExceeded) as exc:
            if not stop:
                stop.append(exc)
        except KiteError as exc:
            m.failed = True
            report.errors[m.symbol] = str(exc)[:300]
            async with pool.acquire() as conn:
                await _save_cursor(conn, m, "error", str(exc)[:500], 0, clock.now())
            log.warning("intraday %s failed (%s..%s): %s", m.symbol, w_start, w_end, exc)

    try:
        async with pool.acquire() as conn:
            await _check_budget(conn, report, settings)

        # 1. forward catch-up for symbols that already have history (concurrent per symbol)
        async def catch_up(m: Member) -> None:
            if m.newest is None:
                return
            # start ON the newest stored session: re-fetching it is the drift re-check
            check = trading_date_of(m.newest, MARKET_TZ)
            cursor = check
            last = final_day(m)
            while cursor <= last and not m.failed and not stop:
                upto = min(cursor + timedelta(days=window_days - 1), last)
                await guarded(m, cursor, upto, False, check if cursor == check else None)
                cursor = upto + timedelta(days=1)

        await asyncio.gather(*(catch_up(m) for m in members if m.newest and not m.failed))
        if stop:
            raise stop[0]
        # 2. newest-first backfill rounds across all symbols
        while True:
            todo = [m for m in members if not m.floored and not m.failed]
            if not todo:
                break
            report.rounds += 1
            async with pool.acquire() as conn:
                await _check_budget(conn, report, settings)
            batch = []
            for m in todo:
                w_end = (m.oldest - timedelta(days=1)) if m.oldest else final_day(m)
                if w_end < floor:
                    m.floored = True
                    continue
                w_start = max(floor, w_end - timedelta(days=window_days - 1))
                batch.append(guarded(m, w_start, w_end, backward=True))
            # round barrier: every symbol's window k completes before any window k+1
            await asyncio.gather(*batch)
            if stop:
                raise stop[0]
            async with pool.acquire() as conn:
                await refresh_exchange_sessions(conn, settings, clock.now())
    except KiteAuthError as exc:
        report.stopped_reason = f"Kite session unavailable: {exc}"
        log.error("intraday backfill stopped: %s", exc)
    except BudgetExceeded as exc:
        report.stopped_reason = str(exc)
        log.warning("intraday backfill stopped: %s", exc)
    finally:
        async with pool.acquire() as conn:
            await refresh_exchange_sessions(conn, settings, clock.now())
            async with conn.transaction():
                await refresh_session_status(conn, settings, clock.now())
            report.db_size_gb = round(await db_size_gb(conn), 3)
        emit()
    log.info("intraday backfill: %s", report.totals())
    return report
