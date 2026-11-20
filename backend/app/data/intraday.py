"""Intraday (1-minute) model: validation, the local symbol-id mapping, and the ONE derivation.

Every higher intraday timeframe is computed on read by the SQL function app.intraday_bars()
(migration 0004) — the single definition shared by live scanning and backtesting. Python code
must go through `derive_bars()` here; never resample 1m bars in pandas (that would break the
backtest↔live parity contract at bar edges).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from app.broker.kite.client import CandleParseError, parse_candle
from app.core.tradingdate import MARKET_TZ
from app.data.validate import INT8_MAX, Rejected

TIMEFRAME = "1m"
INT4_MAX = 2_147_483_647
SID_LIMIT = 32_000  # smallint headroom guard (the universe is ~224 symbols)

# derived timeframes → bucket width in minutes (1m is the stored source itself)
DERIVED_MINUTES: dict[str, int] = {"3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


@dataclass(frozen=True)
class MinuteBar:
    sid: int
    ts: datetime
    open: int
    high: int
    low: int
    close: int
    volume: int

    def values(self) -> tuple[int, int, int, int, int]:
        return (self.open, self.high, self.low, self.close, self.volume)

    def record(self) -> tuple[datetime, int, int, int, int, int, int]:
        """Column order of app.ohlcv_1m: ts, volume, open, high, low, close, sid."""
        return (self.ts, self.volume, self.open, self.high, self.low, self.close, self.sid)


@dataclass
class MinuteValidated:
    clean: list[MinuteBar]
    rejected: list[Rejected]


def validate_minute(symbol: str, sid: int, records: list[Any]) -> MinuteValidated:
    """Reject → quarantine: unparseable · non_canonical_ts (not minute-aligned) ·
    non_positive_price · ohlc_order · negative_volume · out_of_range (price beyond int4
    paisa, or volume beyond bigint — Kite has served 2**64-2, an unsigned underflow) ·
    duplicate_in_batch (every copy of a minute seen twice — neither is trusted)."""
    clean: list[MinuteBar] = []
    rejected: list[Rejected] = []

    def reject(ts: datetime | None, reason: str, detail: str, raw: Any) -> None:
        rejected.append(Rejected(symbol, TIMEFRAME, ts, reason, detail, raw))

    for raw in records:
        try:
            c = parse_candle(raw)
        except CandleParseError as exc:
            reject(None, "unparseable", str(exc), raw)
            continue
        if c.ts.second or c.ts.microsecond:
            reject(c.ts, "non_canonical_ts", f"{c.ts.isoformat()} is not minute-aligned", raw)
            continue
        prices = (c.open, c.high, c.low, c.close)
        if min(prices) <= 0:
            reject(c.ts, "non_positive_price", f"o={c.open} h={c.high} l={c.low} c={c.close}", raw)
        elif max(prices) > INT4_MAX:
            reject(c.ts, "out_of_range", f"price above int4 paisa: {max(prices)}", raw)
        elif c.high < max(c.open, c.close, c.low) or c.low > min(c.open, c.close):
            reject(c.ts, "ohlc_order", f"o={c.open} h={c.high} l={c.low} c={c.close}", raw)
        elif c.volume < 0:
            reject(c.ts, "negative_volume", f"v={c.volume}", raw)
        elif c.volume > INT8_MAX:
            reject(c.ts, "out_of_range", f"volume above bigint: {c.volume}", raw)
        else:
            clean.append(MinuteBar(sid, c.ts, c.open, c.high, c.low, c.close, c.volume))

    counts = Counter(b.ts for b in clean)
    unique = []
    for b in clean:
        if counts[b.ts] > 1:
            reject(
                b.ts,
                "duplicate_in_batch",
                f"{counts[b.ts]} rows for {b.ts.isoformat()}",
                [b.ts.isoformat(), *b.values()],
            )
        else:
            unique.append(b)
    unique.sort(key=lambda b: b.ts)
    return MinuteValidated(unique, rejected)


async def ensure_sids(conn: Any, symbols: list[str]) -> dict[str, int]:
    """Stable symbol → smallint id; creates missing ids, never reuses or deletes."""
    if symbols:
        await conn.executemany(
            "INSERT INTO app.instrument_ids (symbol) VALUES ($1) ON CONFLICT (symbol) DO NOTHING",
            [(s,) for s in symbols],
        )
    rows = await conn.fetch(
        "SELECT symbol, sid FROM app.instrument_ids WHERE symbol = ANY($1)", symbols
    )
    mapping = {r["symbol"]: int(r["sid"]) for r in rows}
    if mapping and max(mapping.values()) > SID_LIMIT:
        raise RuntimeError(f"instrument_ids approaching smallint limit ({max(mapping.values())})")
    return mapping


@dataclass(frozen=True)
class DerivedBar:
    bucket: datetime
    open: int
    high: int
    low: int
    close: int
    volume: int
    minutes: int  # how many 1m bars formed it (a short count = a real hole or session end)
    anchored: bool  # False = no calendar row for that session; 09:15 open assumed


def _session_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Whole IST trading days only — a mid-session start would fabricate a partial edge bar."""
    return (
        datetime.combine(start, time.min, MARKET_TZ),
        datetime.combine(end + timedelta(days=1), time.min, MARKET_TZ),
    )


async def derive_bars(
    conn: Any, symbol: str, timeframe: str, start: date, end: date
) -> list[DerivedBar]:
    """Bars for `timeframe` over whole trading days [start, end], from the 1m source."""
    if timeframe == TIMEFRAME:
        width = timedelta(minutes=1)
    elif timeframe in DERIVED_MINUTES:
        width = timedelta(minutes=DERIVED_MINUTES[timeframe])
    else:
        raise ValueError(f"unsupported intraday timeframe {timeframe!r}")
    if end < start:
        raise ValueError("end before start")
    lo, hi = _session_bounds(start, end)
    rows = await conn.fetch(
        "SELECT * FROM app.intraday_bars($1, $2::interval, $3, $4)", symbol, width, lo, hi
    )
    return [
        DerivedBar(
            r["bucket"],
            r["open"],
            r["high"],
            r["low"],
            r["close"],
            r["volume"],
            r["minutes"],
            r["session_open"] is not None,
        )
        for r in rows
    ]
