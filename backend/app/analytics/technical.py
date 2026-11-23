"""Phase 2 technical analytics over validated Phase 1 candles."""

from __future__ import annotations

from asyncio import to_thread
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import groupby, pairwise
from typing import Any

from app.analytics.indicators import Bar, calculate
from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ, canonical_daily_ts
from app.data.candles import INTERVALS, CandleRequestError, UnknownSymbol, candles
from app.data.intraday import DERIVED_MINUTES


def _date(ts: int) -> date:
    return datetime.fromtimestamp(ts, MARKET_TZ).date()


def _bars(page: dict[str, Any]) -> list[Bar]:
    return [Bar(b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]) for b in page["bars"]]


@dataclass(frozen=True)
class Calendar:
    sessions: Sequence[date]
    opens: Mapping[date, int]
    intraday_quality: Mapping[date, str]
    not_traded: frozenset[date]


def segmented_bars(
    bars: Sequence[Bar], calendar: Calendar, minutes: int | None
) -> list[tuple[list[Bar], str | None]]:
    """Break state at known missing sessions and exclude unverified intraday days."""
    ordinal = {day: i for i, day in enumerate(calendar.sessions)}
    out: list[tuple[list[Bar], str | None]] = []
    prev: Bar | None = None
    for day, group in groupby(bars, key=lambda bar: _date(bar.t)):
        items = list(group)
        reason = None
        if day not in ordinal:
            reason = "session_calendar_unavailable"
        elif minutes and calendar.intraday_quality.get(day) != "complete":
            reason = "intraday_session_" + calendar.intraday_quality.get(day, "unknown")
        elif minutes and (
            any(b.t - a.t != minutes * 60 for a, b in pairwise(items))
            or any(
                (b.t - calendar.opens[day]) % (minutes * 60) for b in items if day in calendar.opens
            )
        ):
            reason = "intraday_candle_gap"
        elif minutes and day not in calendar.opens:
            reason = "session_open_unavailable"
        prev_day = _date(prev.t) if prev else None
        gap = prev_day is not None and any(
            d not in calendar.not_traded
            for d in calendar.sessions[ordinal[prev_day] + 1 : ordinal.get(day, 0)]
        )
        if reason is not None:
            out.append((items, reason))
            prev = None
            continue
        if prev is None or gap:
            out.append((items, None))
        else:
            out[-1][0].extend(items)
        prev = items[-1]
    return out


def pivot_levels(previous: Bar) -> dict[str, float]:
    pivot = (previous.high + previous.low + previous.close) / 3
    mid = (previous.high + previous.low) / 2
    other = 2 * pivot - mid
    return {
        "pivot_paisa": pivot,
        "cpr_top_paisa": max(mid, other),
        "cpr_bottom_paisa": min(mid, other),
    }


def session_indicators(
    bars: Sequence[Bar],
    calendar: Calendar,
    daily: Mapping[date, Bar],
    minutes: int | None,
    opening_minutes: int,
) -> dict[int, dict[str, float | None]]:
    """Bar-typical-price VWAP and opening range, only when the observed prefix starts at open."""
    output: dict[int, dict[str, float | None]] = {}
    previous = dict(zip(calendar.sessions[1:], calendar.sessions, strict=False))
    for day, group in groupby(bars, key=lambda b: _date(b.t)):
        daily_previous = daily.get(previous[day]) if day in previous else None
        base = (
            {**pivot_levels(daily_previous), "previous_close_paisa": daily_previous.close}
            if daily_previous
            else {}
        )
        opened = calendar.opens.get(day)
        volume = weighted = 0
        opening_high = opening_low = None
        complete_prefix = bool(opened is not None and minutes)
        session_open_price = None
        for i, bar in enumerate(group):
            values: dict[str, float | None] = {
                "vwap_paisa": None,
                "opening_range_high_paisa": None,
                "opening_range_low_paisa": None,
                "pivot_paisa": base.get("pivot_paisa"),
                "cpr_top_paisa": base.get("cpr_top_paisa"),
                "cpr_bottom_paisa": base.get("cpr_bottom_paisa"),
                "gap_pct": None,
            }
            if minutes and opened is not None:
                complete_prefix = complete_prefix and bar.t == opened + i * minutes * 60
                if complete_prefix:
                    if i == 0:
                        session_open_price = bar.open
                    volume += bar.volume
                    weighted += (bar.high + bar.low + bar.close) * bar.volume
                    values["vwap_paisa"] = weighted / (3 * volume) if volume else None
                    if opening_minutes % minutes == 0:
                        if bar.t < opened + opening_minutes * 60:
                            opening_high = max(opening_high or bar.high, bar.high)
                            opening_low = min(opening_low or bar.low, bar.low)
                        if bar.t + minutes * 60 >= opened + opening_minutes * 60:
                            values["opening_range_high_paisa"] = opening_high
                            values["opening_range_low_paisa"] = opening_low
            elif minutes is None:
                session_open_price = bar.open
            if daily_previous and session_open_price is not None:
                values["gap_pct"] = 100 * (session_open_price / daily_previous.close - 1)
            if daily_previous:
                values["previous_close_paisa"] = daily_previous.close
            output[bar.t] = values
    return output


def technical_series(
    bars: Sequence[Bar],
    calendar: Calendar,
    daily: Mapping[date, Bar],
    benchmark: Mapping[int, int],
    minutes: int | None,
    settings: Settings,
    include_scoring_inputs: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    periods = settings.analytics_periods
    minimum = periods.minimum_history()
    for segment, reason in segmented_bars(bars, calendar, minutes):
        if reason:
            rows.extend(
                {"t": bar.t, "usable_bars": 0, "status": reason, "values": None} for bar in segment
            )
            continue
        curves = calculate(segment, periods)
        sessions = session_indicators(
            segment, calendar, daily, minutes, settings.analytics_opening_range_minutes
        )
        lookback = periods.relative_strength
        missing_benchmark = 0
        for i, bar in enumerate(segment):
            missing_benchmark += bar.t not in benchmark
            if i > lookback:
                missing_benchmark -= segment[i - lookback - 1].t not in benchmark
            rs = (
                100
                * (
                    bar.close
                    * benchmark[segment[i - lookback].t]
                    / (segment[i - lookback].close * benchmark[bar.t])
                    - 1
                )
                if i >= lookback and not missing_benchmark
                else None
            )
            values = {name: curve[i] for name, curve in curves.items()}
            values.update(sessions[bar.t])
            values["relative_strength_pct"] = rs
            if include_scoring_inputs:
                values["close_paisa"] = bar.close
                values["high_paisa"] = bar.high
                values["low_paisa"] = bar.low
                values["close_change_paisa"] = bar.close - segment[i - 1].close if i else None
            else:
                values.pop("previous_close_paisa", None)
            rows.append(
                {
                    "t": bar.t,
                    "usable_bars": i + 1,
                    "status": "ok" if i + 1 >= minimum else "warming_up",
                    "values": {
                        key: round(value) if key.endswith("_paisa") and value is not None else value
                        for key, value in values.items()
                    },
                }
            )
    return rows


async def _calendar(
    conn: Any, symbol: str, benchmark: str | None, low: date, high: date, minutes: int | None
) -> Calendar:
    exchange = symbol.split(":", 1)[0]
    sessions = await conn.fetch(
        """SELECT (ts AT TIME ZONE 'Asia/Kolkata')::date AS day
           FROM app.ohlcv_candles
           WHERE symbol = $1 AND timeframe = '1d' AND ts >= $2 AND ts < $3 ORDER BY ts""",
        benchmark,
        canonical_daily_ts(low - timedelta(days=31), MARKET_TZ),
        canonical_daily_ts(high + timedelta(days=1), MARKET_TZ),
    )
    opens: dict[date, int] = {}
    quality: dict[date, str] = {}
    not_traded: frozenset[date] = frozenset()
    if minutes:
        opens = {
            row["session_date"]: int(row["session_open"].timestamp())
            for row in await conn.fetch(
                """SELECT session_date, session_open FROM app.exchange_sessions
                   WHERE exchange = $1 AND session_date BETWEEN $2 AND $3""",
                exchange,
                low,
                high,
            )
        }
        quality = {
            row["session_date"]: row["status"]
            for row in await conn.fetch(
                """SELECT session_date, status FROM app.intraday_session_status
                   WHERE sid = (SELECT sid FROM app.instrument_ids WHERE symbol = $1)
                     AND session_date BETWEEN $2 AND $3""",
                symbol,
                low,
                high,
            )
        }
    # A date is a real exchange session when a trusted benchmark daily bar or exchange
    # session exists. Verified no-trade equity days do not break a warm-up segment.
    not_traded = frozenset(
        row["trade_date"]
        for row in await conn.fetch(
            """SELECT fs.trade_date FROM app.nse_archive_files fs
               WHERE fs.trade_date BETWEEN $2 AND $3 AND fs.status = 'ok'
                 AND NOT EXISTS (SELECT 1 FROM app.nse_bhav b
                                 WHERE b.trade_date = fs.trade_date AND b.symbol = $1)
                 AND EXISTS (SELECT 1 FROM app.universe u
                             WHERE u.symbol = $1 AND u.kind = 'equity')""",
            symbol,
            low,
            high,
        )
    )
    return Calendar(sorted({r["day"] for r in sessions} | opens.keys()), opens, quality, not_traded)


async def indicator_report(
    conn: Any,
    symbol: str,
    interval: str,
    before: datetime,
    count: int,
    settings: Settings,
    *,
    include_scoring_inputs: bool = False,
) -> dict[str, Any]:
    """Use the candle service plus exchange/session quality. No analysis of the unfinished day."""
    if interval not in INTERVALS or interval == "W":
        raise CandleRequestError("technical analytics supports 1m, 3m, 5m, 15m, 30m, 1h and D")
    if not 1 <= count <= settings.analytics_max_result_bars:
        raise CandleRequestError(f"count must be 1..{settings.analytics_max_result_bars}")
    minutes = None if interval == "D" else DERIVED_MINUTES.get(interval, 1)
    local = before.astimezone(MARKET_TZ)
    cutoff = settings.intraday_final_after_ist if minutes else settings.daily_bar_final_next_day_ist
    # The exchange calendar decides whether a date traded, including special weekend sessions.
    lag = (not minutes) + (local.time() < cutoff)
    final_day = local.date() - timedelta(days=lag)
    close_boundary = canonical_daily_ts(final_day + timedelta(days=1), MARKET_TZ)
    anchor = min(before, close_boundary)
    warmup = max(settings.analytics_warmup_bars, settings.analytics_periods.minimum_history() - 1)
    limit = count + warmup
    source = await candles(conn, symbol, interval, "adjusted", anchor, limit, limit)
    bars = [
        b
        for b in _bars(source)
        if minutes is None or _date(b.t) >= settings.intraday_analysis_start
    ]
    benchmark = settings.intraday_benchmarks.get(symbol.split(":", 1)[0])
    rows = []
    latest_session = None
    if bars:
        low, high = _date(bars[0].t), _date(bars[-1].t)
        calendar = await _calendar(conn, symbol, benchmark, low, max(high, final_day), minutes)
        latest_session = calendar.sessions[-1] if calendar.sessions else None
        daily = bars
        if minutes:
            daily_limit = (high - low).days + 32
            daily_page = await candles(
                conn, symbol, "D", "adjusted", anchor, daily_limit, daily_limit
            )
            daily = _bars(daily_page)
        comparison: dict[int, int] = {}
        if benchmark == symbol:
            comparison = {b.t: b.close for b in bars}
        elif benchmark:
            try:
                comparison_page = await candles(
                    conn, benchmark, interval, "adjusted", anchor, limit, limit
                )
            except UnknownSymbol:
                pass
            else:
                comparison = {b.t: b.close for b in _bars(comparison_page) if b.close > 0}
        series = await to_thread(
            technical_series,
            bars,
            calendar,
            {_date(b.t): b for b in daily},
            comparison,
            minutes,
            settings,
            include_scoring_inputs,
        )
        rows = series[-count:]
    return {
        "symbol": symbol,
        "interval": interval,
        "source": source["source"],
        "price_basis": "currently_stored_adjusted_history",
        "before": before.astimezone(UTC).isoformat(),
        "benchmark": benchmark,
        "latest_benchmark_session": latest_session.isoformat() if latest_session else None,
        "behind_benchmark": _date(bars[-1].t) < latest_session if bars and latest_session else None,
        "status": rows[-1]["status"] if rows else "no_usable_bars",
        "lookback_bars": len(bars),
        "requested_warmup_bars": warmup,
        "minimum_suite_bars": settings.analytics_periods.minimum_history(),
        "intraday_analysis_start": settings.intraday_analysis_start.isoformat()
        if minutes
        else None,
        "rows": rows,
    }
