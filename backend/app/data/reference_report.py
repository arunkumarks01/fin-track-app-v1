"""Read-only coverage and data views for Phase 1e reference sources."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.data.instruments import GIFT_NIFTY_SYMBOL, INDIA_VIX_SYMBOL, REFERENCE_INDEX_SYMBOLS
from app.data.nse_reference import DATASETS, FIELDS

# ``fo_bhav`` is loaded by the separate F&O history job.  It shares the
# reference-files ledger with the Phase 1e sources, so the read-only status
# view must recognise it even though it is not a selectable 1e ingest source.
REFERENCE_LEDGER_DATASETS = (*DATASETS, "fo_bhav")


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


async def reference_ledger(conn: Any) -> dict[str, Any]:
    """Per dataset: days by status, the stored range, rows rejected and NSE rounding gaps."""
    rows = await conn.fetch(
        """SELECT dataset, status, count(*) AS n, min(trade_date) AS lo, max(trade_date) AS hi,
                  sum(rejected) AS rejected,
                  count(*) FILTER (WHERE detail LIKE '%nse_total_gap%') AS rounding
           FROM app.reference_files GROUP BY 1, 2"""
    )
    out: dict[str, Any] = {
        ds: {
            "ok": 0,
            "missing": 0,
            "error": 0,
            "oldest": None,
            "newest": None,
            "rejected": 0,
            "rounding_days": 0,
        }
        for ds in REFERENCE_LEDGER_DATASETS
    }
    for r in rows:
        d = out[r["dataset"]]
        d[r["status"]] = r["n"]
        d["rejected"] += int(r["rejected"] or 0)
        d["rounding_days"] += r["rounding"]
        if r["status"] == "ok":
            d["oldest"], d["newest"] = _iso(r["lo"]), _iso(r["hi"])
    return out


async def reference_counts(conn: Any, today: date) -> dict[str, Any]:
    row = await conn.fetchrow(
        """SELECT (SELECT count(*) FROM app.news_items) AS news_items,
                  (SELECT count(*) FROM app.news_feeds WHERE last_status = 'error') AS news_errors,
                  (SELECT count(*) FROM app.news_feeds WHERE last_status = 'refused')
                      AS news_refusals,
                  (SELECT count(*) FROM app.economic_events WHERE event_date >= $1)
                      AS calendar_upcoming,
                  (SELECT count(DISTINCT series_id) FROM app.macro_series) AS fred_series,
                  (SELECT count(*) FROM app.macro_series_meta
                   WHERE last_status IN ('error', 'refused')) AS fred_errors""",
        today,
    )
    index_rows = await conn.fetch(
        """SELECT r.symbol,
               (SELECT count(*) FROM app.ohlcv_candles
                WHERE symbol = r.symbol AND timeframe = '1d') AS daily_bars,
               (SELECT (ts AT TIME ZONE 'Asia/Kolkata')::date FROM app.ohlcv_candles
                WHERE symbol = r.symbol AND timeframe = '1d'
                ORDER BY ts DESC LIMIT 1) AS daily_date,
               (SELECT coalesce(sum(s.bars), 0) FROM app.intraday_sessions s
                JOIN app.instrument_ids i USING (sid) WHERE i.symbol = r.symbol) AS minute_bars,
               (SELECT last_ts FROM app.intraday_sessions s
                JOIN app.instrument_ids i USING (sid) WHERE i.symbol = r.symbol
                ORDER BY session_date DESC LIMIT 1) AS minute_at
           FROM unnest($1::text[]) AS r(symbol)""",
        list(REFERENCE_INDEX_SYMBOLS),
    )
    indices = {
        i["symbol"]: {
            "daily_bars": i["daily_bars"],
            "daily_date": _iso(i["daily_date"]),
            "minute_bars": i["minute_bars"],
            "minute_at": i["minute_at"] and i["minute_at"].isoformat(),
        }
        for i in index_rows
    }
    return {
        **dict(row),
        "gift_nifty": indices[GIFT_NIFTY_SYMBOL],
        "india_vix": indices[INDIA_VIX_SYMBOL],
    }


async def participant_positions(conn: Any, days: int) -> list[dict[str, Any]]:
    """The latest `days` sessions of participant OI and volume, newest first, as stored
    (integer contracts; nets are left to the reader)."""
    cols = ", ".join(FIELDS)
    rows = await conn.fetch(
        f"""SELECT trade_date, kind, client_type, {cols}
            FROM app.participant_positions
            WHERE trade_date IN (SELECT DISTINCT trade_date FROM app.participant_positions
                                 ORDER BY trade_date DESC LIMIT $1)
            ORDER BY trade_date DESC, kind, client_type""",  # noqa: S608 — fixed column names
        days,
    )
    by_day: dict[date, dict[str, Any]] = {}
    for r in rows:
        day = by_day.setdefault(
            r["trade_date"], {"date": _iso(r["trade_date"]), "oi": {}, "volume": {}}
        )
        day[r["kind"]][r["client_type"]] = {f: r[f] for f in FIELDS}
    return list(by_day.values())


async def delivery_history(conn: Any, symbol: str, days: int) -> dict[str, Any]:
    """One stock's recent delivery (newest first) and its volume-weighted average over them."""
    rows = await conn.fetch(
        """SELECT trade_date, series, traded_qty, deliverable_qty, deliv_bps
           FROM app.delivery_daily WHERE symbol = $1
           ORDER BY trade_date DESC LIMIT $2""",
        symbol,
        days,
    )
    span = await conn.fetchrow(
        """SELECT count(*) AS n, min(trade_date) AS lo, max(trade_date) AS hi
           FROM app.delivery_daily WHERE symbol = $1""",
        symbol,
    )
    traded = sum(r["traded_qty"] for r in rows)
    deliverable = sum(r["deliverable_qty"] for r in rows)
    avg = (deliverable * 20_000 + traded) // (traded * 2) if traded else None  # bps, half up
    return {
        "days_stored": span["n"],
        "oldest": _iso(span["lo"]),
        "newest": _iso(span["hi"]),
        "window": len(rows),
        "avg_bps": avg,
        "recent": [
            {
                "date": _iso(r["trade_date"]),
                "series": r["series"],
                "traded_qty": r["traded_qty"],
                "deliverable_qty": r["deliverable_qty"],
                "deliv_bps": r["deliv_bps"],
            }
            for r in rows
        ],
    }


async def recent_news(conn: Any, limit: int) -> dict[str, Any]:
    feeds = await conn.fetch(
        """SELECT source, last_status, last_detail, last_fetched_at, items_last, ttl_minutes
           FROM app.news_feeds ORDER BY source"""
    )
    items = await conn.fetch(
        """SELECT source, title, link, summary, published_at, fetched_at FROM app.news_items
           ORDER BY coalesce(published_at, fetched_at) DESC LIMIT $1""",
        limit,
    )
    return {
        "feeds": [
            {
                "source": f["source"],
                "status": f["last_status"],
                "detail": f["last_detail"],
                "fetched_at": f["last_fetched_at"] and f["last_fetched_at"].isoformat(),
                "items_last": f["items_last"],
            }
            for f in feeds
        ],
        "items": [
            {
                "source": i["source"],
                "title": i["title"],
                "link": i["link"],
                "summary": i["summary"],
                "published_at": i["published_at"] and i["published_at"].isoformat(),
                "fetched_at": i["fetched_at"].isoformat(),
            }
            for i in items
        ],
    }


async def macro_latest(conn: Any) -> list[dict[str, Any]]:
    """Each FRED series: its last two observations (value_scaled + scale) and fetch status."""
    rows = await conn.fetch(
        """SELECT m.*, o.obs_date, o.value_scaled FROM app.macro_series_meta m
           LEFT JOIN LATERAL (
               SELECT obs_date, value_scaled FROM app.macro_series s
               WHERE s.series_id = m.series_id ORDER BY obs_date DESC LIMIT 2
           ) o ON true
           ORDER BY m.series_id, o.obs_date DESC"""
    )
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        series = out.setdefault(
            r["series_id"],
            {
                "series_id": r["series_id"],
                "title": r["title"],
                "unit": r["unit"],
                "scale": r["scale"],
                "status": r["last_status"],
                "detail": r["last_detail"],
                "fetched_at": r["last_fetched_at"] and r["last_fetched_at"].isoformat(),
                "observations": [],
            },
        )
        if r["obs_date"] is not None:
            series["observations"].append(
                {"date": _iso(r["obs_date"]), "value_scaled": r["value_scaled"]}
            )
    return list(out.values())


async def latest_market_cap(conn: Any, symbol: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """SELECT trade_date, series, issue_size, market_cap_paisa FROM app.market_cap_daily
           WHERE symbol = $1 ORDER BY trade_date DESC LIMIT 1""",
        symbol,
    )
    if row is None:
        return None
    return {
        "date": row["trade_date"].isoformat(),
        "series": row["series"],
        # Both fields are bigint in Postgres and may exceed JavaScript's safe integer range.
        "issue_size": str(row["issue_size"]),
        "market_cap_paisa": str(row["market_cap_paisa"]),
    }


async def stock_events(conn: Any, symbol: str, today: date, days: int) -> dict[str, Any]:
    """Deals, announcements and board meetings for one stock (newest first)."""
    deals = await conn.fetch(
        """SELECT trade_date, kind, client, side, quantity, price_paisa, price_exact, remarks
           FROM app.deals WHERE symbol = $1 AND trade_date BETWEEN $2 AND $3
           ORDER BY trade_date DESC, kind, client""",
        symbol,
        today - timedelta(days=days),
        today,
    )
    ann = await conn.fetch(
        """SELECT announced_on, category, body FROM app.announcements WHERE symbol = $1
           ORDER BY announced_on DESC, id DESC LIMIT 30""",
        symbol,
    )
    bm = await conn.fetch(
        """SELECT meeting_date, purpose, is_results, first_seen FROM app.board_meetings
           WHERE symbol = $1 ORDER BY meeting_date DESC LIMIT 12""",
        symbol,
    )
    return {
        "deals": [
            {
                "date": _iso(d["trade_date"]),
                "kind": d["kind"],
                "client": d["client"],
                "side": d["side"],
                "quantity": d["quantity"],
                "price_paisa": d["price_paisa"],
                "price_exact": d["price_exact"],
                "remarks": d["remarks"],
            }
            for d in deals
        ],
        "announcements": [
            {"date": _iso(a["announced_on"]), "category": a["category"], "body": a["body"]}
            for a in ann
        ],
        "board_meetings": [
            {
                "date": _iso(b["meeting_date"]),
                "purpose": b["purpose"],
                "is_results": b["is_results"],
                "first_seen": _iso(b["first_seen"]),
            }
            for b in bm
        ],
    }
