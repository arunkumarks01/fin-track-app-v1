"""One stock, everything we hold about it — read-only (the /stocks page, DEC-STOCKPAGE).

Every number here comes from the same queries as the Admin page and the Phase-1 gate
(coverage.py, intraday_coverage.py, the measured adjustments), so the two never disagree.
Nothing is fetched from outside and nothing is written.
"""

from __future__ import annotations

import bisect
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ
from app.data.coverage import symbol_coverage
from app.data.intraday_coverage import completeness
from app.data.nse_archive import PD_SERIES as SHARE_SERIES
from app.data.reference_report import delivery_history, latest_market_cap, stock_events

DELIVERY_DAYS = 20  # the recent window shown on the page (and its weighted average)
EVENT_DAYS = 365  # bulk/block deals shown for the last year

# how Kite's measured prices relate to one NSE corporate action
#   matched       Kite's price change matches the action
#   cant_check    Kite changed prices here, but the action's size can't be checked (no amount…)
#   mismatch      Kite changed prices here by a different amount
#   no_change     Kite's prices did not change for it (common for dividends)
#   not_measured  too little measured data around the date to tell
_EVENT_STATUS = {
    "explained": "matched",
    "attributed": "cant_check",
    "unexplained_small": "mismatch",
    "suspect": "mismatch",
    "corroborated": "mismatch",
    "official_mismatch": "mismatch",
}

_MAX_NAME_HOPS = 20  # a rename chain longer than this is a data error, not history


def _iso(d: date | datetime | None) -> str | None:
    return d.isoformat() if d else None


def _f(x: Decimal | float | None) -> float | None:
    return None if x is None else float(x)


async def stock_profile(
    conn: Any, settings: Settings, now: datetime, symbol: str
) -> dict[str, Any] | None:
    member = await conn.fetchrow(
        "SELECT symbol, kind, active FROM app.universe WHERE symbol = $1", symbol
    )
    if member is None:
        return None
    exchange, tradingsymbol = symbol.split(":", 1)
    kind = member["kind"]
    inst = await conn.fetchrow(
        """SELECT name, tick_size, lot_size FROM app.instruments
           WHERE exchange = $1 AND tradingsymbol = $2 AND segment IN ('NSE', 'BSE', 'INDICES')
           ORDER BY segment LIMIT 1""",
        exchange,
        tradingsymbol,
    )
    fut = None
    if kind == "equity":
        fut = await conn.fetchrow(
            """SELECT lot_size, expiry FROM app.instruments
               WHERE segment = 'NFO-FUT' AND instrument_type = 'FUT' AND name = $1
                 AND expiry >= $2::date
               ORDER BY expiry LIMIT 1""",
            tradingsymbol,
            now.date(),
        )

    return {
        "symbol": symbol,
        "exchange": exchange,
        "tradingsymbol": tradingsymbol,
        "kind": kind,
        "active": member["active"],
        "name": inst["name"] if inst else None,
        "tick_size_paisa": inst["tick_size"] if inst and kind == "equity" else None,
        "futures": ({"lot_size": fut["lot_size"], "expiry": _iso(fut["expiry"])} if fut else None),
        "names": await _name_history(conn, tradingsymbol),
        "daily": await _daily(conn, settings, now, symbol),
        "minute": await _minute(conn, settings, symbol),
        "actions": await _actions(conn, symbol),
        "other_security_actions": await conn.fetchval(
            """SELECT count(*) FROM (SELECT 1 FROM app.corporate_actions WHERE symbol = $1
               GROUP BY ex_date, purpose
               HAVING NOT bool_or(series = ANY($2::text[]))) g""",
            symbol,
            list(SHARE_SERIES),
        ),
        "unlisted_steps": await _unlisted_steps(conn, settings, symbol),
        "delivery": await delivery_history(conn, symbol, DELIVERY_DAYS),
        "market_cap": await latest_market_cap(conn, symbol),
        "events": await stock_events(conn, symbol, now.astimezone(MARKET_TZ).date(), EVENT_DAYS),
        "as_traded_days": {
            r["quality"]: r["n"]
            for r in await conn.fetch(
                """SELECT quality, count(*) AS n FROM app.adjustment_days
                   WHERE symbol = $1 GROUP BY 1 ORDER BY 1""",
                symbol,
            )
        },
    }


async def _name_history(conn: Any, tradingsymbol: str) -> list[dict[str, Any]]:
    """Earlier NSE names of this stock, newest change first (NSE symbolchange.csv)."""
    out: list[dict[str, Any]] = []
    current, seen = tradingsymbol, {tradingsymbol}
    for _ in range(_MAX_NAME_HOPS):
        r = await conn.fetchrow(
            """SELECT old_symbol, new_symbol, effective_date, company FROM app.symbol_changes
               WHERE new_symbol = $1 ORDER BY effective_date DESC LIMIT 1""",
            current,
        )
        if r is None or r["old_symbol"] in seen:
            break
        out.append(
            {
                "old": r["old_symbol"],
                "new": r["new_symbol"],
                "date": _iso(r["effective_date"]),
                "company": r["company"],
            }
        )
        current = r["old_symbol"]
        seen.add(current)
    return out


async def _reasons(conn: Any, table: str, column: str, symbol: str, tf: str) -> dict[str, int]:
    sql = (
        f"SELECT {column} AS k, count(*) AS n FROM app.{table} "  # noqa: S608 — fixed names
        "WHERE symbol = $1 AND timeframe = $2 GROUP BY 1 ORDER BY 2 DESC"
    )
    return {r["k"]: r["n"] for r in await conn.fetch(sql, symbol, tf)}


async def _daily(conn: Any, settings: Settings, now: datetime, symbol: str) -> dict[str, Any]:
    cov = await symbol_coverage(conn, settings, now, symbol)
    row = None
    if cov is not None:
        row = {
            k: (v.isoformat() if isinstance(v, date) else v)
            for k, v in asdict(cov).items()
            if k != "symbol"
        }
    return {
        "coverage": row,
        "quarantined": await _reasons(conn, "ohlcv_quarantine", "reason", symbol, "1d"),
        "flagged": await _reasons(conn, "ohlcv_flags", "flag", symbol, "1d"),
    }


async def _minute(conn: Any, settings: Settings, symbol: str) -> dict[str, Any]:
    rows = await completeness(conn, symbol)
    row = None
    if rows:
        c = rows[0]
        row = {
            **{
                k: (v.isoformat() if isinstance(v, date) else v)
                for k, v in asdict(c).items()
                if k != "symbol"
            },
            "complete_pct": c.complete_pct,
        }
    return {
        "analysis_start": settings.intraday_analysis_start.isoformat(),
        "coverage": row,
        "quarantined": await _reasons(conn, "ohlcv_quarantine", "reason", symbol, "1m"),
    }


async def _actions(conn: Any, symbol: str) -> list[dict[str, Any]]:
    """NSE's corporate actions (one row per ex-date + purpose, across series), newest first,
    each with how Kite's MEASURED prices relate to it. Share series only (EQ/BE/BZ), exactly
    as the adjustment analysis reads them; bond/preference-share actions are counted apart."""
    acts = await conn.fetch(
        """SELECT ex_date, purpose, array_agg(id ORDER BY id) AS ids,
                  array_agg(DISTINCT series ORDER BY series) AS series,
                  max(record_date) AS record_date,
                  min(kinds) AS kinds,  -- parsed from the purpose: same in every series
                  max(split_from_paisa) AS split_from, max(split_to_paisa) AS split_to,
                  max(bonus_new) AS bonus_new, max(bonus_held) AS bonus_held,
                  max(dividend_paisa) AS dividend
           FROM app.corporate_actions WHERE symbol = $1 AND series = ANY($2::text[])
           GROUP BY ex_date, purpose ORDER BY ex_date DESC, purpose""",
        symbol,
        list(SHARE_SERIES),
    )
    events = await conn.fetch(
        """SELECT effective_date, action_ids, classification, detail FROM app.adjustment_events
           WHERE symbol = $1 AND cardinality(action_ids) > 0""",
        symbol,
    )
    days = await conn.fetch(
        "SELECT trade_date, window_no, quality FROM app.adjustment_days WHERE symbol = $1",
        symbol,
    )
    by_day = {r["trade_date"]: r for r in days}
    ordered = sorted(by_day)
    out = []
    for a in acts:
        ids = set(a["ids"])
        ev = next((e for e in events if ids & set(e["action_ids"])), None)
        if ev is not None:
            status, detail = _EVENT_STATUS.get(ev["classification"], "mismatch"), ev["detail"]
        else:
            status, detail = _no_event_status(a["ex_date"], ordered, by_day), None
        out.append(
            {
                "ex_date": _iso(a["ex_date"]),
                "record_date": _iso(a["record_date"]),
                "purpose": a["purpose"],
                "kinds": list(a["kinds"] or []),
                "series": list(a["series"]),
                "split_from_paisa": a["split_from"],
                "split_to_paisa": a["split_to"],
                "bonus_new": a["bonus_new"],
                "bonus_held": a["bonus_held"],
                "dividend_paisa": a["dividend"],
                "kite": status,
                "detail": detail,
            }
        )
    return out


def _no_event_status(ex: date, ordered: list[date], by_day: dict[date, Any]) -> str:
    """No measured step at the action: Kite didn't change its prices for it — but only if the
    trading days either side were both measured in the SAME window. Otherwise we can't tell."""
    i = bisect.bisect_left(ordered, ex)
    if i == 0 or i >= len(ordered):
        return "not_measured"
    before, on = by_day[ordered[i - 1]], by_day[ordered[i]]
    same = before["window_no"] == on["window_no"]
    measured = before["quality"] == "measured" and on["quality"] == "measured"
    return "no_change" if same and measured else "not_measured"


async def _unlisted_steps(conn: Any, settings: Settings, symbol: str) -> list[dict[str, Any]]:
    """Kite price changes at dates where NSE lists no action, newest first. Steps below
    ADJ_REVIEW_MIN_STEP are marked small (refit noise, as on the Admin page)."""
    min_step = Decimal(str(settings.adj_review_min_step))
    rows = await conn.fetch(
        """SELECT effective_date, ratio, classification, magnitude, detail
           FROM app.adjustment_events
           WHERE symbol = $1 AND cardinality(action_ids) = 0
           ORDER BY effective_date DESC""",
        symbol,
    )
    return [
        {
            "date": _iso(r["effective_date"]),
            "ratio": _f(r["ratio"]),
            "class": r["classification"],
            "magnitude": _f(r["magnitude"]),
            "small": r["classification"] == "unexplained_small"
            and r["magnitude"] is not None
            and r["magnitude"] < min_step,
            "detail": r["detail"],
        }
        for r in rows
    ]
