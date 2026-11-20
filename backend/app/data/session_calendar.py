"""Per-instrument session calendar (revised DEC-1B, 2026-11-20).

A session's expected minute count is each instrument's OWN norm — never a cross-sectional
benchmark/percentile (those failed on real data: NIFTY 50's own series is gappy, outage days
leave everyone low, and the 2026 closing auction rolled out per instrument: 375 → 360 minutes
on 2026-08-03 for 206 equities, later for 12 more, never for indices).

  norm      mode of the instrument's first INTRADAY_NORM_BASELINE_SESSIONS sessions; it is
            re-baselined only on a PERSISTENT step-change — a new length held for
            INTRADAY_NORM_STEP_SESSIONS consecutive sessions becomes the norm from its first
            session (the auction switch is a new normal, not a gap; a week-long feed gap isn't)
  market-wide short day
            ≥ INTRADAY_MARKET_SHORT_SHARE of an exchange's judged instruments (at least
            INTRADAY_MARKET_MIN_INSTRUMENTS) are below their own norm that day (outage,
            muhurat, half-day): those short sessions are 'market_short' — reported, not flagged
  statuses  complete | short | excess | market_short | unknown (no norm yet)
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.core.config import Settings


@dataclass(frozen=True)
class CalendarRules:
    baseline_sessions: int
    step_sessions: int
    market_short_share: float
    market_min_instruments: int

    @classmethod
    def from_settings(cls, s: Settings) -> CalendarRules:
        return cls(
            s.intraday_norm_baseline_sessions,
            s.intraday_norm_step_sessions,
            s.intraday_market_short_share,
            s.intraday_market_min_instruments,
        )


def instrument_norms(counts: Sequence[int], rules: CalendarRules) -> list[int | None]:
    """The instrument's own expected length for each of its sessions (in date order)."""
    n = len(counts)
    if n < rules.step_sessions:
        return [None] * n
    base = Counter(counts[: rules.baseline_sessions]).most_common()
    top = base[0][1]
    norm = max(v for v, k in base if k == top)  # ties → the longer session
    out: list[int | None] = []
    for i, c in enumerate(counts):
        window = counts[i : i + rules.step_sessions]
        if c != norm and len(window) == rules.step_sessions and all(x == c for x in window):
            norm = c
        out.append(norm)
    return out


Row = tuple[date, int, int | None, str]  # (session, bars, expected, status)


def classify(
    data: Mapping[str, Sequence[tuple[date, int]]],
    exchange_of: Mapping[str, str],
    rules: CalendarRules,
) -> dict[str, list[Row]]:
    norms = {k: instrument_norms([c for _, c in sorted(v)], rules) for k, v in data.items()}
    judged: dict[tuple[str, date], int] = defaultdict(int)
    short: dict[tuple[str, date], int] = defaultdict(int)
    for k, v in data.items():
        for (d, c), exp in zip(sorted(v), norms[k], strict=True):
            if exp is not None:
                judged[(exchange_of[k], d)] += 1
                short[(exchange_of[k], d)] += c < exp
    market_short = {
        key
        for key, n in judged.items()
        if n >= rules.market_min_instruments and short[key] / n >= rules.market_short_share
    }
    out: dict[str, list[Row]] = {}
    for k, v in data.items():
        rows: list[Row] = []
        for (d, c), exp in zip(sorted(v), norms[k], strict=True):
            if exp is None:
                st = "unknown"
            elif c == exp:
                st = "complete"
            elif c > exp:
                st = "excess"
            elif (exchange_of[k], d) in market_short:
                st = "market_short"
            else:
                st = "short"
            rows.append((d, c, exp, st))
        out[k] = rows
    return out


async def refresh_session_status(conn: Any, settings: Settings, now: datetime) -> int:
    """Recompute every instrument's session statuses (caller may hold a transaction)."""
    rows = await conn.fetch(
        """SELECT x.sid, i.symbol, x.session_date, x.bars
           FROM app.intraday_sessions x JOIN app.instrument_ids i USING (sid)
           ORDER BY x.sid, x.session_date"""
    )
    data: dict[str, list[tuple[date, int]]] = defaultdict(list)
    sid_of: dict[str, int] = {}
    for r in rows:
        data[r["symbol"]].append((r["session_date"], r["bars"]))
        sid_of[r["symbol"]] = r["sid"]
    out = classify(
        data, {k: k.split(":", 1)[0] for k in data}, CalendarRules.from_settings(settings)
    )
    await conn.execute("TRUNCATE app.intraday_session_status")
    records = [(sid_of[k], d, c, exp, st) for k, v in out.items() for d, c, exp, st in v]
    if records:
        await conn.copy_records_to_table(
            "intraday_session_status",
            schema_name="app",
            columns=["sid", "session_date", "bars", "expected", "status"],
            records=records,
        )
    return len(records)
