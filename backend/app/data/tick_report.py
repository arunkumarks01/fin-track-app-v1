"""Read-only status of slice 1d: tick capture, the Parquet export ledger, option-chain snapshots."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any


def _iso(v: date | datetime | None) -> str | None:
    return v.isoformat() if v else None


async def ticks_status(conn: Any, live: dict[str, Any] | None) -> dict[str, Any]:
    runs = await conn.fetch(
        """SELECT session_date, started_at, ended_at, status, instruments, ticks, rejected,
                  reconnects, detail FROM app.tick_capture_runs ORDER BY id DESC LIMIT 10"""
    )
    hot = await conn.fetchrow(
        """SELECT approximate_row_count('app.ticks') AS rows,
                  (SELECT min(ts) FROM app.ticks) AS oldest,
                  (SELECT max(ts) FROM app.ticks) AS newest"""
    )
    exp = await conn.fetchrow(
        """SELECT count(*) FILTER (WHERE status = 'verified') AS verified,
                  count(*) FILTER (WHERE dropped_at IS NOT NULL) AS dropped,
                  count(*) FILTER (WHERE status = 'mismatch') AS mismatch,
                  count(*) FILTER (WHERE status = 'error') AS error,
                  coalesce(sum(rows) FILTER (WHERE status = 'verified'), 0) AS rows,
                  coalesce(sum(bytes) FILTER (WHERE status = 'verified'), 0) AS bytes,
                  max(chunk_start) FILTER (WHERE status = 'verified') AS newest
           FROM app.tick_exports"""
    )
    chain = await conn.fetch(
        """SELECT DISTINCT ON (underlying) underlying, snapshot_ts, status, contracts, spot_paisa
           FROM app.option_snapshot_meta ORDER BY underlying, snapshot_ts DESC"""
    )
    today = await conn.fetchval(
        "SELECT count(DISTINCT snapshot_ts) FROM app.option_snapshot_meta "
        "WHERE snapshot_ts >= date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') "
        "AT TIME ZONE 'Asia/Kolkata'"
    )
    return {
        "capture": live,
        "runs": [
            {
                "session_date": _iso(r["session_date"]),
                "started_at": _iso(r["started_at"]),
                "ended_at": _iso(r["ended_at"]),
                "status": r["status"],
                "instruments": r["instruments"],
                "ticks": r["ticks"],
                "rejected": r["rejected"],
                "reconnects": r["reconnects"],
                "detail": r["detail"],
            }
            for r in runs
        ],
        "hot": {
            "rows_estimate": max(int(hot["rows"] or 0), 0),
            "oldest": _iso(hot["oldest"]),
            "newest": _iso(hot["newest"]),
        },
        "exports": {
            "verified": exp["verified"],
            "dropped": exp["dropped"],
            "mismatch": exp["mismatch"],
            "error": exp["error"],
            "rows": int(exp["rows"]),
            "bytes": int(exp["bytes"]),
            "newest": _iso(exp["newest"]),
        },
        "chain": {
            "snapshots_today": today,
            "latest": [
                {
                    "underlying": c["underlying"],
                    "snapshot_ts": _iso(c["snapshot_ts"]),
                    "status": c["status"],
                    "contracts": c["contracts"],
                    "spot_paisa": c["spot_paisa"],
                }
                for c in chain
            ],
        },
    }


async def latest_chain(conn: Any, underlying: str) -> dict[str, Any] | None:
    """The newest stored snapshot of one underlying: raw rows grouped by expiry and strike."""
    meta = await conn.fetchrow(
        """SELECT snapshot_ts, spot_paisa, fut_paisa, fut_expiry, contracts, status
           FROM app.option_snapshot_meta WHERE underlying = $1 ORDER BY snapshot_ts DESC LIMIT 1""",
        underlying,
    )
    if meta is None:
        return None
    rows = await conn.fetch(
        """SELECT expiry, strike_paisa, opt_type, ltp, volume, oi, bid, bid_qty, ask, ask_qty, ltt
           FROM app.option_snapshots WHERE underlying = $1 AND snapshot_ts = $2
           ORDER BY expiry, strike_paisa, opt_type""",
        underlying,
        meta["snapshot_ts"],
    )
    return {
        "underlying": underlying,
        "snapshot_ts": _iso(meta["snapshot_ts"]),
        "spot_paisa": meta["spot_paisa"],
        "fut_paisa": meta["fut_paisa"],
        "fut_expiry": _iso(meta["fut_expiry"]),
        "status": meta["status"],
        "rows": [
            {
                "expiry": _iso(r["expiry"]),
                "strike_paisa": r["strike_paisa"],
                "type": r["opt_type"],
                "ltp": r["ltp"],
                "volume": r["volume"],
                "oi": r["oi"],
                "bid": r["bid"],
                "bid_qty": r["bid_qty"],
                "ask": r["ask"],
                "ask_qty": r["ask_qty"],
                "ltt": _iso(r["ltt"]),
            }
            for r in rows
        ],
    }
