"""Read-only reports over the measured adjustments, the NSE archive and audited repairs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.core.config import Settings
from app.data.extract_daily import last_final_daily_session


async def adjustments_summary(conn: Any, settings: Settings, now: datetime) -> dict[str, Any]:
    last_final = last_final_daily_session(now, settings.daily_bar_final_next_day_ist)
    ledger = {
        r["status"]: {"dates": r["n"], "oldest": r["lo"], "newest": r["hi"]}
        for r in await conn.fetch(
            """SELECT status, count(*) AS n, min(trade_date) AS lo, max(trade_date) AS hi
               FROM app.nse_archive_files GROUP BY 1"""
        )
    }
    quality = {
        r["quality"]: r["n"]
        for r in await conn.fetch(
            "SELECT quality, count(*) AS n FROM app.adjustment_days GROUP BY 1 ORDER BY 1"
        )
    }
    events = {
        r["classification"]: r["n"]
        for r in await conn.fetch(
            "SELECT classification, count(*) AS n FROM app.adjustment_events GROUP BY 1 ORDER BY 1"
        )
    }
    kinds = {
        r["k"]: r["n"]
        for r in await conn.fetch(
            "SELECT unnest(kinds) AS k, count(*) AS n FROM app.corporate_actions GROUP BY 1"
        )
    }
    stale = await conn.fetch(
        """SELECT u.symbol, t.tf, c.checked_on
           FROM app.universe u CROSS JOIN (VALUES ('1d'), ('1m')) t(tf)
           LEFT JOIN app.adjustment_checks c ON c.symbol = u.symbol AND c.timeframe = t.tf
           WHERE u.active AND (c.checked_on IS NULL OR c.checked_on < $1)""",
        last_final,
    )
    review = await conn.fetch(
        """SELECT symbol, effective_date, classification, detail FROM app.adjustment_events
           WHERE classification IN ('suspect', 'official_mismatch', 'unexplained_small')
           ORDER BY classification = 'suspect' DESC, classification = 'official_mismatch' DESC,
                    effective_date DESC LIMIT 100"""
    )
    return {
        "generated_at": now.isoformat(),
        "last_final_session": last_final.isoformat(),
        "nse_archive": {
            k: {"dates": v["dates"], "oldest": _iso(v["oldest"]), "newest": _iso(v["newest"])}
            for k, v in ledger.items()
        },
        "corporate_actions": {
            "rows": await conn.fetchval("SELECT count(*) FROM app.corporate_actions"),
            "by_kind": kinds,
        },
        "nse_bhav_rows": await conn.fetchval("SELECT count(*) FROM app.nse_bhav"),
        "days_by_quality": quality,
        "events_by_class": events,
        "drift_events": await conn.fetchval("SELECT count(*) FROM app.adjustment_drift"),
        "checks_stale": len(stale),
        "review": [
            {
                "symbol": r["symbol"],
                "date": r["effective_date"].isoformat(),
                "class": r["classification"],
                "detail": r["detail"],
            }
            for r in review
        ],
    }


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


async def symbol_adjustments(conn: Any, symbol: str) -> dict[str, Any]:
    windows = await conn.fetch(
        """SELECT window_no, min(trade_date) AS first, max(trade_date) AS last,
                  max(f_daily) AS f, max(g_minute) AS g, max(v_daily) AS v,
                  count(*) AS days,
                  count(*) FILTER (WHERE quality = 'measured') AS measured,
                  count(*) FILTER (WHERE quality = 'blip') AS blips,
                  count(*) FILTER (WHERE quality = 'suspect') AS suspect,
                  count(*) FILTER (WHERE quality IN ('carried', 'unmeasurable')) AS unmeasured
           FROM app.adjustment_days WHERE symbol = $1 GROUP BY 1 ORDER BY 1""",
        symbol,
    )
    events = await conn.fetch(
        """SELECT e.effective_date, e.ratio, e.g_ratio, e.classification, e.detail
           FROM app.adjustment_events e WHERE e.symbol = $1 ORDER BY 1""",
        symbol,
    )
    drift = await conn.fetch(
        """SELECT timeframe, ref_date, ratio, detected_at FROM app.adjustment_drift
           WHERE symbol = $1 ORDER BY ref_date""",
        symbol,
    )
    return {
        "symbol": symbol,
        "windows": [
            {
                "window": r["window_no"],
                "first": r["first"].isoformat(),
                "last": r["last"].isoformat(),
                "f_daily": _f(r["f"]),
                "g_minute": _f(r["g"]),
                "v_daily": _f(r["v"]),
                "days": r["days"],
                "measured": r["measured"],
                "blips": r["blips"],
                "suspect": r["suspect"],
                "unmeasured": r["unmeasured"],
            }
            for r in windows
        ],
        "events": [
            {
                "date": r["effective_date"].isoformat(),
                "ratio": _f(r["ratio"]),
                "g_ratio": _f(r["g_ratio"]),
                "class": r["classification"],
                "detail": r["detail"],
            }
            for r in events
        ],
        "drift": [
            {
                "timeframe": r["timeframe"],
                "ref_date": r["ref_date"].isoformat(),
                "ratio": _f(r["ratio"]),
                "detected_at": r["detected_at"].isoformat(),
            }
            for r in drift
        ],
    }


def _f(x: Any) -> float | None:
    return None if x is None else float(x)


async def repairs_report(conn: Any) -> dict[str, Any]:
    """Every audited 1d bar repair, with the acceptance check: the repaired close must equal NSE's
    official close for that day (or the difference is an explained Kite adjustment)."""
    rows = await conn.fetch(
        """SELECT r.symbol, (r.ts AT TIME ZONE 'Asia/Kolkata')::date AS d,
                  (r.old_values->>'close')::bigint AS old_close,
                  (r.new_values->>'close')::bigint AS new_close,
                  b.close AS official, r.repaired_at
           FROM app.data_repairs r
           LEFT JOIN app.nse_bhav b
                  ON b.symbol = r.symbol AND b.trade_date = (r.ts AT TIME ZONE 'Asia/Kolkata')::date
           WHERE r.timeframe = '1d'
           ORDER BY r.symbol, r.ts"""
    )
    purged = await conn.fetchval(
        "SELECT count(*) FROM app.data_repairs WHERE timeframe = 'nse_bhav'"
    )
    items = [
        {
            "symbol": r["symbol"],
            "date": r["d"].isoformat(),
            "old_close": r["old_close"],
            "new_close": r["new_close"],
            "official_close": r["official"],
            "matches_official": None if r["official"] is None else r["new_close"] == r["official"],
        }
        for r in rows
    ]
    checked = [i for i in items if i["matches_official"] is not None]
    return {
        "repairs": len(items),
        "checked_against_official": len(checked),
        "match_official": sum(1 for i in checked if i["matches_official"]),
        "not_checkable_yet": len(items) - len(checked),
        "archive_rows_purged": purged,  # audited deletions of wrong NSE rows (not bar repairs)
        "mismatches": [i for i in checked if not i["matches_official"]],
        "items": items,
    }
