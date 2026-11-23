"""Read stored option snapshots and derive analytics without altering source data."""

from __future__ import annotations

from asyncio import to_thread
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from itertools import groupby
from typing import Any, cast

from app.analytics.options import Contract, OptionType, constant_tenor_iv, expiry_analytics, iv_rank
from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ, canonical_daily_ts, trading_date_of

# NSE closing-auction timetable in DEC-1D-TICKS and the two session regimes in CLAUDE.md.
_AUCTION_TRANSITION = date(2026, 12, 7)
_OLD_DERIVATIVES_CLOSE = time(15, 40)
_NEW_DERIVATIVES_CLOSE = time(15, 45)
_NEW_INDEX_EXPIRY_CLOSE = time(15, 30)
_SECONDS_PER_YEAR = timedelta(days=365).total_seconds()


def years_to_expiry(expiry: date, snapshot_at: datetime, index: bool) -> float:
    close = _OLD_DERIVATIVES_CLOSE
    if expiry >= _AUCTION_TRANSITION:
        close = _NEW_INDEX_EXPIRY_CLOSE if index else _NEW_DERIVATIVES_CLOSE
    return (
        datetime.combine(expiry, close, MARKET_TZ) - snapshot_at
    ).total_seconds() / _SECONDS_PER_YEAR


def _contract(row: Mapping[str, Any]) -> Contract:
    return Contract(
        row["expiry"],
        row["strike_paisa"],
        cast(OptionType, row["opt_type"]),
        row["bid"],
        row["ask"],
        row["bid_qty"],
        row["ask_qty"],
        row["oi"],
        row["volume"],
    )


def _expiry_reports(
    rows: Sequence[Contract],
    previous: Mapping[tuple[date, int, OptionType], Contract],
    spot: int | None,
    at: datetime,
    index: bool,
    rate: tuple[float, float] | None,
    ceiling: float,
    complete: bool,
) -> list[dict[str, Any]]:
    out = []
    for expiry, items in groupby(rows, key=lambda r: r.expiry):
        years = years_to_expiry(expiry, at, index)
        report = expiry_analytics(list(items), previous, spot, years, rate, ceiling, complete)
        out.append({"expiry": expiry.isoformat(), "days_to_expiry": years * 365, **report})
    return out


def _tenor(reports: Sequence[Mapping[str, Any]], days: int) -> float | None:
    points = {
        r["days_to_expiry"]: r["atm_iv_pct"] / 100 for r in reports if r["atm_iv_pct"] is not None
    }
    return constant_tenor_iv(points, days)


async def _previous_session(
    conn: Any, underlying: str, lower: datetime, cutoff: datetime
) -> tuple[datetime | None, dict[tuple[date, int, OptionType], Contract]]:
    rows = await conn.fetch(
        """SELECT o.snapshot_ts, o.expiry, o.strike_paisa, o.opt_type,
                  o.bid, o.ask, o.bid_qty, o.ask_qty, o.oi, o.volume
           FROM app.option_snapshots o WHERE o.underlying = $1 AND o.snapshot_ts = (
               SELECT snapshot_ts FROM app.option_snapshot_meta
               WHERE underlying = $1 AND snapshot_ts >= $2 AND snapshot_ts < $3 AND status = 'ok'
               ORDER BY snapshot_ts DESC LIMIT 1)""",
        underlying,
        lower,
        cutoff,
    )
    if not rows:
        return None, {}
    contracts = [_contract(r) for r in rows]
    return rows[0]["snapshot_ts"], {contract.key: contract for contract in contracts}


async def _tenor_history(
    conn: Any,
    underlying: str,
    lower: datetime,
    cutoff: datetime,
    sample_at: time,
    index: bool,
    rates: tuple[float, float],
    settings: Settings,
) -> list[float]:
    rows = await conn.fetch(
        """WITH daily AS (
               SELECT DISTINCT ON (local_day) m.snapshot_ts, m.spot_paisa,
                      (m.snapshot_ts AT TIME ZONE 'Asia/Kolkata')::date AS local_day
               FROM app.option_snapshot_meta m
               WHERE m.underlying = $1 AND m.snapshot_ts >= $2 AND m.snapshot_ts < $3
                 AND m.status = 'ok' AND m.spot_paisa IS NOT NULL
                 AND (m.snapshot_ts AT TIME ZONE 'Asia/Kolkata')::time <= $4
               ORDER BY local_day, m.snapshot_ts DESC
           ), nearest AS (
               SELECT o.snapshot_ts, d.spot_paisa, o.expiry, o.strike_paisa, o.opt_type,
                      o.bid, o.ask, o.bid_qty, o.ask_qty, o.oi, o.volume,
                      dense_rank() OVER (PARTITION BY o.snapshot_ts, o.expiry
                          ORDER BY abs(o.strike_paisa::bigint - d.spot_paisa),
                                   o.strike_paisa) AS rank
               FROM daily d JOIN app.option_snapshots o ON o.snapshot_ts = d.snapshot_ts
               WHERE o.underlying = $1 AND o.snapshot_ts >= $2 AND o.snapshot_ts < $3
           )
           SELECT * FROM nearest WHERE rank = 1 ORDER BY snapshot_ts, expiry, opt_type""",
        underlying,
        lower,
        cutoff,
        sample_at,
    )

    def derive() -> list[float]:
        values = []
        for at, group in groupby(rows, key=lambda r: r["snapshot_ts"]):
            sample = list(group)
            reports = _expiry_reports(
                [_contract(r) for r in sample],
                {},
                sample[0]["spot_paisa"],
                at,
                index,
                rates,
                settings.analytics_option_vol_ceiling,
                True,
            )
            iv = _tenor(reports, settings.analytics_iv_tenor_days)
            if iv is not None:
                values.append(iv)
        return values

    return await to_thread(derive)


async def option_report(
    conn: Any,
    underlying: str,
    as_of: datetime,
    rate_bps: int | None,
    dividend_bps: int | None,
    settings: Settings,
) -> dict[str, Any]:
    lower = as_of - timedelta(days=settings.analytics_iv_history_calendar_days)
    meta = await conn.fetchrow(
        """SELECT snapshot_ts, spot_paisa, fut_paisa, fut_expiry, status, contracts
           FROM app.option_snapshot_meta WHERE underlying = $1 AND snapshot_ts BETWEEN $2 AND $3
           ORDER BY snapshot_ts DESC LIMIT 1""",
        underlying,
        lower,
        as_of,
    )
    if meta is None:
        return {
            "underlying": underlying,
            "status": "no_snapshot",
            "as_of": as_of.isoformat(),
            "expiries": [],
        }
    at: datetime = meta["snapshot_ts"]
    rows = await conn.fetch(
        """SELECT expiry, strike_paisa, opt_type, bid, ask, bid_qty, ask_qty, oi, volume
           FROM app.option_snapshots WHERE underlying = $1 AND snapshot_ts = $2
           ORDER BY expiry, strike_paisa, opt_type""",
        underlying,
        at,
    )
    previous_days = canonical_daily_ts(trading_date_of(at, MARKET_TZ), MARKET_TZ)
    lower = at - timedelta(days=settings.analytics_iv_history_calendar_days)
    earlier_at, previous = await _previous_session(conn, underlying, lower, previous_days)
    rates = (
        None
        if rate_bps is None or dividend_bps is None
        else (rate_bps / 10000, dividend_bps / 10000)
    )
    index = underlying in settings.chain_index_underlyings
    complete = meta["status"] == "ok" and len(rows) == meta["contracts"]
    reports = _expiry_reports(
        [_contract(r) for r in rows],
        previous,
        meta["spot_paisa"],
        at,
        index,
        rates,
        settings.analytics_option_vol_ceiling,
        complete,
    )
    current = _tenor(reports, settings.analytics_iv_tenor_days)
    history = (
        await _tenor_history(
            conn,
            underlying,
            lower,
            previous_days,
            at.astimezone(MARKET_TZ).time(),
            index,
            rates,
            settings,
        )
        if current is not None and rates is not None
        else []
    )
    age = int((as_of - at).total_seconds())
    return {
        "underlying": underlying,
        "as_of": as_of.isoformat(),
        "snapshot_at": at.isoformat(),
        "snapshot_age_seconds": age,
        "stale": age > settings.analytics_option_snapshot_max_age_seconds,
        "status": meta["status"] if complete or meta["status"] != "ok" else "partial",
        "coverage": "captured_strikes_only",
        "oi_basis": "as_reported_by_kite",
        "spot_paisa": meta["spot_paisa"],
        "near_future_paisa": meta["fut_paisa"],
        "near_future_expiry": meta["fut_expiry"].isoformat() if meta["fut_expiry"] else None,
        "pricing_model": "Black-Scholes-Merton; positive two-sided quote midpoint; ACT/365 fixed",
        "rate_bps": rate_bps,
        "dividend_bps": dividend_bps,
        "oi_comparison_snapshot_at": earlier_at.isoformat() if earlier_at else None,
        "iv_rank": {
            "tenor_days": settings.analytics_iv_tenor_days,
            "current_iv_pct": current * 100 if current is not None else None,
            "rank_pct": iv_rank(current, history, settings.analytics_iv_min_observations),
            "observations": len(history) + (current is not None),
            "minimum_observations": settings.analytics_iv_min_observations,
        },
        "expiries": reports,
    }
