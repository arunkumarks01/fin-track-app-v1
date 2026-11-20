"""The validation gate (ARCHITECTURE §5). Every bar passes here before it can be stored.

Reject → quarantine (kept with raw payload + reason, never silently dropped):
  unparseable · non_positive_price · ohlc_order · negative_volume · out_of_range (a value
  beyond bigint — e.g. Kite's 2**64-2 volume, an unsigned underflow) · duplicate_in_batch ·
  index_gap (indices only).
Flag but keep: large_gap (equities: suspected corporate action) · missing_sessions ·
  retimed_ts (Kite's legacy off-midnight daily stamp, re-stamped to 00:00 IST).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from app.broker.kite.client import CandleParseError, parse_candle
from app.core.tradingdate import MARKET_TZ, canonical_daily_ts, trading_date_of

INT8_MAX = 2**63 - 1  # Postgres bigint — anything beyond is garbage, never storable


@dataclass(frozen=True)
class Bar:
    symbol: str
    timeframe: str
    ts: datetime
    open: int
    high: int
    low: int
    close: int
    volume: int
    oi: int | None = None

    def values(self) -> tuple[int, int, int, int, int, int | None]:
        return (self.open, self.high, self.low, self.close, self.volume, self.oi)


@dataclass(frozen=True)
class Rejected:
    symbol: str
    timeframe: str
    ts: datetime | None
    reason: str
    detail: str
    raw: Any


@dataclass(frozen=True)
class Flag:
    symbol: str
    timeframe: str
    ts: datetime
    flag: str
    detail: str


@dataclass
class Validated:
    clean: list[Bar] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)


@dataclass(frozen=True)
class PrevBar:
    ts: datetime
    close: int


def weekdays_between(a: date, b: date) -> int:
    """Weekdays strictly between a and b (exclusive). Holidays are not known here."""
    if b <= a:
        return 0
    count, d = 0, a + timedelta(days=1)
    while d < b:
        if d.weekday() < 5:
            count += 1
        d += timedelta(days=1)
    return count


def _bar_problem(bar: Bar) -> tuple[str, str] | None:
    values = (bar.open, bar.high, bar.low, bar.close, bar.volume, bar.oi or 0)
    if max(values) > INT8_MAX:
        return "out_of_range", f"value above bigint: {max(values)}"
    if min(bar.open, bar.high, bar.low, bar.close) <= 0:
        return "non_positive_price", f"o={bar.open} h={bar.high} l={bar.low} c={bar.close}"
    if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close):
        return "ohlc_order", f"o={bar.open} h={bar.high} l={bar.low} c={bar.close}"
    if bar.volume < 0 or (bar.oi is not None and bar.oi < 0):
        return "negative_volume", f"v={bar.volume} oi={bar.oi}"
    return None


def validate_daily(
    symbol: str,
    records: list[Any],
    *,
    prev: PrevBar | None,
    large_gap_pct: int,
    missing_weekdays_flag: int,
    is_index: bool = False,
    index_max_gap_pct: int | None = None,
) -> Validated:
    """Validate one Kite response for one symbol.

    Off-midnight daily timestamps (Kite's pre-Dec-2018 data uses 09:15 IST) are re-stamped
    to 00:00 IST of their IST trading date and flagged 'retimed_ts'. When a trading date has
    exactly one native 00:00 bar, it wins and any re-stamped copies are quarantined; any
    other multiplicity quarantines every copy. Indices quarantine an implausible gap
    ('index_gap'); equities flag-but-keep it (suspected corporate action).
    """
    out = Validated()
    native: dict[datetime, list[tuple[Bar, Any]]] = {}
    retimed: dict[datetime, list[tuple[Bar, Any, datetime]]] = {}
    for raw in records:
        try:
            c = parse_candle(raw)
        except CandleParseError as exc:
            out.rejected.append(Rejected(symbol, "1d", None, "unparseable", str(exc), raw))
            continue
        canonical = canonical_daily_ts(trading_date_of(c.ts, MARKET_TZ), MARKET_TZ)
        bar = Bar(symbol, "1d", canonical, c.open, c.high, c.low, c.close, c.volume, c.oi)
        problem = _bar_problem(bar)
        if problem:
            out.rejected.append(Rejected(symbol, "1d", bar.ts, problem[0], problem[1], raw))
            continue
        if c.ts == canonical:
            native.setdefault(canonical, []).append((bar, raw))
        else:
            retimed.setdefault(canonical, []).append((bar, raw, c.ts))

    unique: list[Bar] = []
    for ts in sorted(native.keys() | retimed.keys()):
        nat, ret = native.get(ts, []), retimed.get(ts, [])
        day = trading_date_of(ts, MARKET_TZ)
        if len(nat) == 1:
            unique.append(nat[0][0])
            for _, raw, orig in ret:
                out.rejected.append(
                    Rejected(
                        symbol,
                        "1d",
                        ts,
                        "duplicate_in_batch",
                        f"re-timed copy ({orig.isoformat()}) lost to native 00:00 bar ({day})",
                        raw,
                    )
                )
        elif not nat and len(ret) == 1:
            bar, _, orig = ret[0]
            unique.append(bar)
            out.flags.append(
                Flag(
                    symbol,
                    "1d",
                    ts,
                    "retimed_ts",
                    f"Kite ts {orig.isoformat()} re-stamped to 00:00 IST",
                )
            )
        else:
            n = len(nat) + len(ret)
            for _, raw in nat:
                out.rejected.append(
                    Rejected(symbol, "1d", ts, "duplicate_in_batch", f"{n} rows for {day}", raw)
                )
            for _, raw, _ in ret:
                out.rejected.append(
                    Rejected(symbol, "1d", ts, "duplicate_in_batch", f"{n} rows for {day}", raw)
                )

    index_limit = index_max_gap_pct if index_max_gap_pct is not None else large_gap_pct
    last = prev
    for b in unique:
        if last is not None:
            gap = abs(b.open - last.close) * 100
            if is_index and gap > index_limit * last.close:
                out.rejected.append(
                    Rejected(
                        symbol,
                        "1d",
                        b.ts,
                        "index_gap",
                        f"open {b.open} vs prev close {last.close} (>{index_limit}%; indices have "
                        "no corporate actions)",
                        [b.ts.isoformat(), *b.values()],
                    )
                )
                out.flags = [f for f in out.flags if f.ts != b.ts]
                continue  # the previous good bar stays the reference
            if gap > large_gap_pct * last.close:
                out.flags.append(
                    Flag(
                        symbol, "1d", b.ts, "large_gap", f"open {b.open} vs prev close {last.close}"
                    )
                )
            missing = weekdays_between(
                trading_date_of(last.ts, MARKET_TZ), trading_date_of(b.ts, MARKET_TZ)
            )
            if missing > missing_weekdays_flag:
                out.flags.append(
                    Flag(
                        symbol, "1d", b.ts, "missing_sessions", f"{missing} weekdays without a bar"
                    )
                )
        out.clean.append(b)
        last = PrevBar(b.ts, b.close)
    return out
