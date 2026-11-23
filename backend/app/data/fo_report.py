"""Read-only coverage of the F&O history (slice 1f)."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.data.nse_fo import FO_DATASET


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


async def fo_summary(conn: Any) -> dict[str, Any]:
    ledger = {
        r["status"]: r["n"]
        for r in await conn.fetch(
            "SELECT status, count(*) AS n FROM app.reference_files WHERE dataset = $1 GROUP BY 1",
            FO_DATASET,
        )
    }
    span = await conn.fetchrow(
        """SELECT min(trade_date) AS lo, max(trade_date) AS hi, count(*) AS rows,
                  count(DISTINCT trade_date) AS days, count(DISTINCT underlying) AS underlyings
           FROM app.fo_daily"""
    )
    by_kind = {
        r["kind"]: r["n"]
        for r in await conn.fetch("SELECT kind, count(*) AS n FROM app.fo_daily GROUP BY 1")
    }
    return {
        "days_ok": ledger.get("ok", 0),
        "days_missing": ledger.get("missing", 0),
        "days_error": ledger.get("error", 0),
        "oldest": _iso(span["lo"]),
        "newest": _iso(span["hi"]),
        "rows": span["rows"],
        "days_with_rows": span["days"],
        "underlyings": span["underlyings"],
        "rows_by_kind": by_kind,
        "table_gb": round(
            float(await conn.fetchval("SELECT pg_total_relation_size('app.fo_daily')")) / 1024**3, 3
        ),
    }
