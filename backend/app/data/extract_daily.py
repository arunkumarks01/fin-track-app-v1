"""Resumable, rate-limited daily-candle extractor (Phase 1a).

Per symbol: resume from `app.ingest_cursor`, walk Kite's per-request window (2000 days for
`day`), validate every record, and commit bars + quarantine + flags + cursor together, one
window per transaction — a crash never leaves a half-written window or a cursor ahead of
the data.

Honesty rules:
- Today's bar is partial until DAILY_BAR_FINAL_AFTER_IST; only completed sessions are fetched.
- The cursor on the newest window advances only to the last bar actually received, so a
  bar Kite publishes late is fetched next run instead of being skipped forever.
- A re-fetched bar that differs from the stored one is quarantined ('conflicts_with_stored');
  the stored bar is never silently overwritten.
- KiteAuthError stops the whole run (no session = no data). Other Kite errors mark the
  symbol 'error' in the cursor and the run moves on.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol

from app.broker.kite.client import KiteAuthError, KiteError
from app.core.clock import Clock
from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ, canonical_daily_ts, trading_date_of
from app.data.validate import Bar, PrevBar, Rejected, Validated, validate_daily

log = logging.getLogger(__name__)

TIMEFRAME = "1d"
KITE_INTERVAL = "day"
SOURCE = "kite:historical:day"


class HistoricalSource(Protocol):
    async def historical_raw(
        self, instrument_token: int, interval: str, start: datetime, end: datetime
    ) -> list[Any]: ...


@dataclass
class SymbolResult:
    symbol: str
    status: str  # 'ok' | 'up_to_date' | 'error' | 'auth_stopped'
    inserted: int = 0
    already_stored: int = 0
    quarantined: int = 0
    flagged: int = 0
    windows: int = 0
    error: str | None = None


@dataclass
class RunReport:
    started_at: datetime
    results: list[SymbolResult] = field(default_factory=list)
    stopped_reason: str | None = None

    def totals(self) -> dict[str, int]:
        keys = ("inserted", "already_stored", "quarantined", "flagged", "windows")
        out = {k: sum(getattr(r, k) for r in self.results) for k in keys}
        out["symbols"] = len(self.results)
        out["errors"] = sum(1 for r in self.results if r.status == "error")
        return out


def last_completed_session(now: datetime, final_after: time) -> date:
    """Latest weekday whose daily bar is final (holidays just yield no bar)."""
    local = now.astimezone(MARKET_TZ)
    d = local.date() if local.time() >= final_after else local.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def date_windows(start: date, end: date, max_days: int) -> list[tuple[date, date]]:
    windows, cursor = [], start
    while cursor <= end:
        stop = min(cursor + timedelta(days=max_days - 1), end)
        windows.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return windows


async def _cursor(conn: Any, symbol: str) -> date | None:
    ts = await conn.fetchval(
        "SELECT last_ts FROM app.ingest_cursor WHERE symbol = $1 AND timeframe = $2",
        symbol,
        TIMEFRAME,
    )
    return None if ts is None else trading_date_of(ts, MARKET_TZ)


async def _prev_bar(conn: Any, symbol: str, before: datetime) -> PrevBar | None:
    row = await conn.fetchrow(
        """SELECT ts, close FROM app.ohlcv_candles
           WHERE symbol = $1 AND timeframe = $2 AND ts < $3 ORDER BY ts DESC LIMIT 1""",
        symbol,
        TIMEFRAME,
        before,
    )
    return None if row is None else PrevBar(row["ts"], row["close"])


async def _set_cursor(
    conn: Any,
    symbol: str,
    last_ts: datetime | None,
    status: str,
    error: str | None,
    added: int,
    now: datetime,
) -> None:
    await conn.execute(
        """
        INSERT INTO app.ingest_cursor (symbol, timeframe, last_ts, status, last_error,
                                       rows_written, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (symbol, timeframe) DO UPDATE SET
            last_ts = COALESCE(EXCLUDED.last_ts, app.ingest_cursor.last_ts),
            status = EXCLUDED.status, last_error = EXCLUDED.last_error,
            rows_written = app.ingest_cursor.rows_written + EXCLUDED.rows_written,
            updated_at = EXCLUDED.updated_at
        """,
        symbol,
        TIMEFRAME,
        last_ts,
        status,
        error,
        added,
        now,
    )


def _quarantine_row(r: Rejected, now: datetime) -> tuple[Any, ...]:
    return (
        r.symbol,
        r.timeframe,
        r.ts,
        r.reason,
        r.detail[:500],
        json.dumps(r.raw, default=str),
        SOURCE,
        now,
    )


async def _commit_window(conn: Any, v: Validated, now: datetime) -> tuple[int, int, list[Rejected]]:
    """Insert new bars; detect conflicts with stored bars. Returns (inserted, same, conflicts)."""
    conflicts: list[Rejected] = []
    inserted = same = 0
    if v.clean:
        existing = {
            r["ts"]: (r["open"], r["high"], r["low"], r["close"], r["volume"], r["oi"])
            for r in await conn.fetch(
                """SELECT ts, open, high, low, close, volume, oi FROM app.ohlcv_candles
                   WHERE symbol = $1 AND timeframe = $2 AND ts = ANY($3::timestamptz[])""",
                v.clean[0].symbol,
                TIMEFRAME,
                [b.ts for b in v.clean],
            )
        }
        fresh: list[Bar] = []
        for b in v.clean:
            stored = existing.get(b.ts)
            if stored is None:
                fresh.append(b)
            elif stored == b.values():
                same += 1
            else:
                conflicts.append(
                    Rejected(
                        b.symbol,
                        TIMEFRAME,
                        b.ts,
                        "conflicts_with_stored",
                        f"stored {stored} vs fetched {b.values()}",
                        [b.ts.isoformat(), *b.values()],
                    )
                )
        if fresh:
            await conn.executemany(
                """INSERT INTO app.ohlcv_candles
                   (symbol, timeframe, ts, open, high, low, close, volume, oi)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                   ON CONFLICT (symbol, timeframe, ts) DO NOTHING""",
                [(b.symbol, TIMEFRAME, b.ts, *b.values()) for b in fresh],
            )
            inserted = len(fresh)
    rejected = v.rejected + conflicts
    if rejected:
        await conn.executemany(
            """INSERT INTO app.ohlcv_quarantine
               (symbol, timeframe, ts, reason, detail, raw, source, quarantined_at)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)
               ON CONFLICT DO NOTHING""",  # same bad row re-fetched: keep one record
            [_quarantine_row(r, now) for r in rejected],
        )
    if v.flags:
        await conn.executemany(
            """INSERT INTO app.ohlcv_flags (symbol, timeframe, ts, flag, detail, flagged_at)
               VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING""",
            [(f.symbol, f.timeframe, f.ts, f.flag, f.detail, now) for f in v.flags],
        )
    return inserted, same, conflicts


async def ingest_symbol(
    pool: Any,
    source: HistoricalSource,
    symbol: str,
    instrument_token: int,
    settings: Settings,
    clock: Clock,
    kind: str = "equity",
) -> SymbolResult:
    result = SymbolResult(symbol, "ok")
    end = last_completed_session(clock.now(), settings.daily_bar_final_after_ist)
    async with pool.acquire() as conn:
        resume = await _cursor(conn, symbol)
    start = settings.ingest_daily_start if resume is None else resume + timedelta(days=1)
    if start > end:
        result.status = "up_to_date"
        return result

    windows = date_windows(start, end, settings.kite_window_days[KITE_INTERVAL])
    for i, (w_start, w_end) in enumerate(windows):
        is_latest = i == len(windows) - 1
        try:
            records = await source.historical_raw(
                instrument_token,
                KITE_INTERVAL,
                datetime.combine(w_start, time.min, MARKET_TZ),
                datetime.combine(w_end, time(23, 59, 59), MARKET_TZ),
            )
        except KiteAuthError:
            result.status = "auth_stopped"
            raise
        except KiteError as exc:
            result.status, result.error = "error", str(exc)[:500]
            async with pool.acquire() as conn:
                await _set_cursor(conn, symbol, None, "error", result.error, 0, clock.now())
            log.warning("daily ingest %s failed in window %s..%s: %s", symbol, w_start, w_end, exc)
            return result

        now = clock.now()
        async with pool.acquire() as conn, conn.transaction():
            prev = await _prev_bar(conn, symbol, canonical_daily_ts(w_start, MARKET_TZ))
            v = validate_daily(
                symbol,
                records,
                prev=prev,
                large_gap_pct=settings.validation_large_gap_pct,
                missing_weekdays_flag=settings.validation_missing_weekdays_flag,
                is_index=kind == "index",
                index_max_gap_pct=settings.validation_index_max_gap_pct,
            )
            inserted, same, conflicts = await _commit_window(conn, v, now)
            if is_latest:
                covered = max((b.ts for b in v.clean), default=None)
            else:
                covered = canonical_daily_ts(w_end, MARKET_TZ)
            await _set_cursor(conn, symbol, covered, "ok", None, inserted, now)
        result.windows += 1
        result.inserted += inserted
        result.already_stored += same
        result.quarantined += len(v.rejected) + len(conflicts)
        result.flagged += len(v.flags)
    return result


async def reset_cursors(conn: Any, symbols: list[str]) -> int:
    """Forget where these symbols got to, so the next run re-fetches their full history.
    Stored bars are untouched: identical re-fetched bars are skipped, differing ones are
    quarantined as conflicts. Refuses an empty list (never a silent full reset)."""
    if not symbols:
        raise ValueError("reset_cursors needs an explicit symbol list")
    result = await conn.execute(
        "DELETE FROM app.ingest_cursor WHERE timeframe = $1 AND symbol = ANY($2)",
        TIMEFRAME,
        symbols,
    )
    return int(result.split()[-1])


async def run_daily_backfill(
    pool: Any,
    source: HistoricalSource,
    settings: Settings,
    clock: Clock,
    symbols: list[str] | None = None,
    *,
    reset: bool = False,
) -> RunReport:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT symbol, instrument_token, kind FROM app.universe
               WHERE active AND ($1::text[] IS NULL OR symbol = ANY($1))
               ORDER BY kind DESC, symbol""",
            symbols,
        )
        if reset:
            if symbols is None:
                raise ValueError("reset requires an explicit symbol list")
            unknown = sorted(set(symbols) - {r["symbol"] for r in rows})
            if unknown:
                raise ValueError(f"not active universe symbols: {unknown}")
            await reset_cursors(conn, symbols)
    report = RunReport(started_at=clock.now())
    if not rows:
        report.stopped_reason = "universe is empty — refresh the universe first"
        return report
    for row in rows:
        try:
            report.results.append(
                await ingest_symbol(
                    pool,
                    source,
                    row["symbol"],
                    row["instrument_token"],
                    settings,
                    clock,
                    kind=row["kind"],
                )
            )
        except KiteAuthError as exc:
            report.results.append(SymbolResult(row["symbol"], "auth_stopped", error=str(exc)))
            report.stopped_reason = f"Kite session unavailable: {exc}"
            log.error("daily backfill stopped at %s: %s", row["symbol"], exc)
            break
    log.info("daily backfill: %s", report.totals())
    return report
