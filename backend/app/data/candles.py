"""Chart data (read-only): paged candles per interval, adjusted or as-traded.

Every interval comes from FinTrack's own data — the chart never re-buckets:
  1m          stored 1-minute bars
  3m … 1h     THE session-anchored derivation (app.intraday_bars via derive_bars, DEC-1B)
  D           stored daily bars (Kite-native)
  W           built here from the daily bars, weeks starting Monday 00:00 IST
Adjustment:
  adjusted    Kite's series as stored, times any recorded Kite re-adjustment (drift) since
  raw         as traded: NSE's official bhavcopy for D/W; the measured as-traded 1m function for
              1m (days whose measurement isn't usable are omitted and counted, never guessed);
              not offered for derived intraday intervals.
Bars flagged 'pre_listing' (ghost bars of another security) are never served.
Prices are integer paisa; the browser converts to rupees at display.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from app.core.tradingdate import MARKET_TZ, canonical_daily_ts
from app.data.intraday import DERIVED_MINUTES, derive_bars

INTERVALS = ("1m", "3m", "5m", "15m", "30m", "1h", "D", "W")
RAW_INTERVALS = ("1m", "D", "W")


class CandleRequestError(ValueError):
    """A request we refuse rather than guess (→ 422)."""


class UnknownSymbol(LookupError):
    """Not in the universe (→ 404)."""


@dataclass(frozen=True)
class Candle:
    t: datetime
    o: int
    h: int
    l: int  # noqa: E741 — the conventional OHLC field name
    c: int
    v: int

    def as_json(self) -> dict[str, int]:
        return {
            "t": int(self.t.timestamp()),
            "o": self.o,
            "h": self.h,
            "l": self.l,
            "c": self.c,
            "v": self.v,
        }


def _ist_date(ts: datetime) -> date:
    return ts.astimezone(MARKET_TZ).date()


async def _drift(conn: Any, symbol: str, timeframe: str) -> list[tuple[date, float]]:
    rows = await conn.fetch(
        "SELECT ref_date, ratio FROM app.adjustment_drift WHERE symbol = $1 AND timeframe = $2",
        symbol,
        timeframe,
    )
    return [(r["ref_date"], float(r["ratio"])) for r in rows]


def _apply_drift(bars: list[Candle], drift: Sequence[tuple[date, float]]) -> list[Candle]:
    """Adjusted-as-of-now: a bar on/before a drift's ref_date is scaled by its ratio."""
    if not drift:
        return bars
    out = []
    for b in bars:
        f = math.prod(r for ref, r in drift if _ist_date(b.t) <= ref)
        if f == 1:
            out.append(b)
        else:
            out.append(
                Candle(
                    b.t,
                    round(b.o * f),
                    round(b.h * f),
                    round(b.l * f),
                    round(b.c * f),
                    round(b.v / f),
                )
            )
    return out


async def _daily(
    conn: Any, symbol: str, raw: bool, before: datetime | None, limit: int
) -> list[Candle]:
    if raw:
        rows = await conn.fetch(
            """SELECT trade_date AS d, open, high, low, close, volume FROM app.nse_bhav
               WHERE symbol = $1 AND ($2::date IS NULL OR trade_date < $2::date)
               ORDER BY trade_date DESC LIMIT $3""",
            symbol,
            before and _ist_date(before - timedelta(seconds=1)) + timedelta(days=1),
            limit,
        )
        return [
            Candle(
                canonical_daily_ts(r["d"], MARKET_TZ),
                r["open"],
                r["high"],
                r["low"],
                r["close"],
                r["volume"],
            )
            for r in reversed(rows)
        ]
    rows = await conn.fetch(
        """SELECT c.ts, c.open, c.high, c.low, c.close, c.volume FROM app.ohlcv_candles c
            WHERE c.symbol = $1 AND c.timeframe = '1d' AND ($2::timestamptz IS NULL OR c.ts < $2)
              AND NOT EXISTS (SELECT 1 FROM app.ohlcv_flags f WHERE f.symbol = c.symbol
                    AND f.timeframe = '1d' AND f.ts = c.ts AND f.flag = 'pre_listing')
            ORDER BY c.ts DESC LIMIT $3""",
        symbol,
        before,
        limit,
    )
    bars = [
        Candle(r["ts"], r["open"], r["high"], r["low"], r["close"], r["volume"])
        for r in reversed(rows)
    ]
    return _apply_drift(bars, await _drift(conn, symbol, "1d"))


def _week_start(d: date) -> datetime:
    monday = d - timedelta(days=d.weekday())
    return datetime.combine(monday, time.min, MARKET_TZ)


async def _weekly(
    conn: Any, symbol: str, raw: bool, before: datetime | None, count: int
) -> tuple[list[Candle], bool]:
    limit = (count + 2) * 6
    days = await _daily(conn, symbol, raw, before, limit)
    groups: dict[datetime, list[Candle]] = {}
    for b in days:
        groups.setdefault(_week_start(_ist_date(b.t)), []).append(b)
    weeks = sorted(groups)
    if len(days) == limit and weeks:  # the oldest week may be cut by the row limit
        weeks = weeks[1:]
    out = [
        Candle(w, g[0].o, max(x.h for x in g), min(x.l for x in g), g[-1].c, sum(x.v for x in g))
        for w in weeks
        for g in [groups[w]]
    ]
    has_more = len(out) > count or len(days) == limit
    return out[-count:], has_more


async def _sid(conn: Any, symbol: str) -> int | None:
    sid: int | None = await conn.fetchval(
        "SELECT sid FROM app.instrument_ids WHERE symbol = $1", symbol
    )
    return sid


async def _minutes(
    conn: Any, symbol: str, raw: bool, before: datetime | None, count: int
) -> tuple[list[Candle], bool, int, datetime | None]:
    """Bars, has_more, omitted count, and the paging cursor: the earliest minute SCANNED, so a
    page whose minutes were all omitted (no usable measurement) still pages on."""
    sid = await _sid(conn, symbol)
    if sid is None:
        return [], False, 0, None
    rows = await conn.fetch(
        """SELECT ts, open, high, low, close, volume FROM app.ohlcv_1m
           WHERE sid = $1 AND ($2::timestamptz IS NULL OR ts < $2)
           ORDER BY ts DESC LIMIT $3""",
        sid,
        before,
        count + 1,
    )
    has_more = len(rows) > count
    rows = list(reversed(rows[:count]))
    if not rows:
        return [], False, 0, None
    cursor: datetime = rows[0]["ts"]
    if not raw:
        bars = [
            Candle(r["ts"], r["open"], r["high"], r["low"], r["close"], r["volume"]) for r in rows
        ]
        return _apply_drift(bars, await _drift(conn, symbol, "1m")), has_more, 0, cursor
    traded = await conn.fetch(
        "SELECT * FROM app.minute_as_traded($1, $2, $3)",
        symbol,
        rows[0]["ts"],
        rows[-1]["ts"] + timedelta(minutes=1),
    )
    bars = [
        Candle(r["ts"], r["open"], r["high"], r["low"], r["close"], r["volume"])
        for r in traded
        if r["close"] is not None
    ]
    return bars, has_more, len(traded) - len(bars), cursor


async def _derived(
    conn: Any, symbol: str, interval: str, before: datetime | None, count: int
) -> tuple[list[Candle], bool]:
    sid = await _sid(conn, symbol)
    if sid is None:
        return [], False
    per_session = math.ceil(375 / DERIVED_MINUTES[interval])
    sessions = await conn.fetch(
        """SELECT session_date FROM app.intraday_sessions
           WHERE sid = $1 AND ($2::date IS NULL OR session_date <= $2::date)
           ORDER BY session_date DESC LIMIT $3""",
        sid,
        before and _ist_date(before),
        math.ceil(count / per_session) + 2,
    )
    if not sessions:
        return [], False
    start, end = sessions[-1]["session_date"], sessions[0]["session_date"]
    derived = [
        b
        for b in await derive_bars(conn, symbol, interval, start, end)
        if before is None or b.bucket < before
    ]
    older = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM app.intraday_sessions WHERE sid = $1 AND session_date < $2)",
        sid,
        start,
    )
    bars = [Candle(b.bucket, b.open, b.high, b.low, b.close, b.volume) for b in derived]
    has_more = bool(older) or len(bars) > count
    return _apply_drift(bars[-count:], await _drift(conn, symbol, "1m")), has_more


SOURCES = {
    ("D", False): "Kite daily (adjusted)",
    ("D", True): "NSE official bhavcopy",
    ("W", False): "weekly from Kite daily (adjusted)",
    ("W", True): "weekly from NSE official bhavcopy",
    ("1m", False): "Kite 1-minute (adjusted)",
    ("1m", True): "measured as-traded 1-minute",
}


async def candles(
    conn: Any,
    symbol: str,
    interval: str,
    adjustment: str,
    before: datetime | None,
    count: int,
    max_bars: int,
) -> dict[str, Any]:
    if interval not in INTERVALS:
        raise CandleRequestError(f"interval must be one of {', '.join(INTERVALS)}")
    if adjustment not in ("adjusted", "raw"):
        raise CandleRequestError("adjustment must be 'adjusted' or 'raw'")
    if not 1 <= count <= max_bars:
        raise CandleRequestError(f"count must be 1..{max_bars}")
    kind = await conn.fetchval("SELECT kind FROM app.universe WHERE symbol = $1", symbol)
    if kind is None:
        raise UnknownSymbol(symbol)
    raw = adjustment == "raw" and kind != "index"  # indices are never adjusted: one series
    if adjustment == "raw" and interval not in RAW_INTERVALS:
        raise CandleRequestError(
            f"as-traded prices are available for {', '.join(RAW_INTERVALS)} only"
        )
    omitted, cursor = 0, None
    if interval == "D":
        rows = await _daily(conn, symbol, raw, before, count + 1)
        has_more, bars = len(rows) > count, rows[-count:]
    elif interval == "W":
        bars, has_more = await _weekly(conn, symbol, raw, before, count)
    elif interval == "1m":
        bars, has_more, omitted, cursor = await _minutes(conn, symbol, raw, before, count)
    else:
        bars, has_more = await _derived(conn, symbol, interval, before, count)
    source = SOURCES.get(
        (interval, raw), "FinTrack session-anchored derivation from Kite 1-minute (adjusted)"
    )
    if kind == "index":
        source = source.replace(" (adjusted)", "") + " (indices are not adjusted)"
    return {
        "symbol": symbol,
        "interval": interval,
        "adjustment": adjustment,
        "source": source,
        "prices": "integer paisa",
        "bars": [b.as_json() for b in bars],
        "has_more": has_more,
        "next_before": int((cursor or bars[0].t).timestamp()) if cursor or bars else None,
        "omitted_unusable": omitted,
    }


async def symbols(conn: Any) -> list[dict[str, str]]:
    rows = await conn.fetch(
        "SELECT symbol, kind FROM app.universe WHERE active ORDER BY kind DESC, symbol"
    )
    return [
        {
            "symbol": r["symbol"],
            "exchange": r["symbol"].split(":", 1)[0],
            "tradingsymbol": r["symbol"].split(":", 1)[1],
            "kind": r["kind"],
        }
        for r in rows
    ]
