"""Calendar-aware 1-minute completeness (Phase 1b).

A session's expected minute count is NOT a hardcoded 375: it is the exchange benchmark's
count that day (app.exchange_sessions), so muhurat / half-day / special sessions are judged
against their real length. Per session:
  complete  bars == expected
  short     bars <  expected   → flagged for review (a real gap, e.g. RELIANCE 360 vs 375)
  excess    bars >  expected   → flagged for review (benchmark itself may be short)
  unknown   benchmark has no data that day → not guessed
Plus `missing_sessions`: benchmark sessions inside a symbol's span with no bars at all.
Nothing here quarantines data — it reports, and later stages decide what is usable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

_SQL = """
WITH s AS (
    SELECT i.symbol, x.session_date, x.bars, e.expected_minutes AS expected
    FROM app.intraday_sessions x
    JOIN app.instrument_ids i USING (sid)
    LEFT JOIN app.exchange_sessions e
           ON e.exchange = split_part(i.symbol, ':', 1) AND e.session_date = x.session_date
),
per AS (
    SELECT symbol, min(session_date) AS first_d, max(session_date) AS last_d,
           count(*) AS sessions,
           sum(bars) AS bars,
           count(*) FILTER (WHERE bars = expected) AS complete,
           count(*) FILTER (WHERE bars < expected) AS short,
           COALESCE(sum(expected - bars) FILTER (WHERE bars < expected), 0) AS missing_minutes,
           count(*) FILTER (WHERE bars > expected) AS excess,
           count(*) FILTER (WHERE expected IS NULL) AS unknown,
           count(*) FILTER (WHERE expected IS NOT NULL) AS known
    FROM s GROUP BY symbol
)
-- missing = benchmark sessions in the symbol's span - the symbol's sessions matched to one.
-- (sid, session_date) is unique, so this is exact — and linear, unlike an anti-join per
-- exchange session against the unindexed CTE (that was quadratic: >5 min on real data).
SELECT p.symbol, p.first_d, p.last_d, p.sessions, p.bars, p.complete, p.short,
       p.missing_minutes, p.excess, p.unknown,
       (SELECT count(*) FROM app.exchange_sessions e
         WHERE e.exchange = split_part(p.symbol, ':', 1)
           AND e.session_date BETWEEN p.first_d AND p.last_d
           AND e.expected_minutes IS NOT NULL
       ) - p.known AS missing_sessions
FROM per p
ORDER BY p.symbol
"""


@dataclass(frozen=True)
class SymbolCompleteness:
    symbol: str
    first: date
    last: date
    sessions: int
    bars: int
    complete: int
    short: int
    missing_minutes: int
    excess: int
    unknown: int
    missing_sessions: int

    @property
    def complete_pct(self) -> float:
        judged = self.sessions - self.unknown
        return round(100 * self.complete / judged, 2) if judged else 0.0

    @property
    def review(self) -> int:
        return self.short + self.excess + self.missing_sessions


async def completeness(conn: Any) -> list[SymbolCompleteness]:
    return [
        SymbolCompleteness(
            r["symbol"],
            r["first_d"],
            r["last_d"],
            r["sessions"],
            int(r["bars"]),
            r["complete"],
            r["short"],
            int(r["missing_minutes"]),
            r["excess"],
            r["unknown"],
            r["missing_sessions"],
        )
        for r in await conn.fetch(_SQL)
    ]


async def review_sessions(conn: Any, symbol: str, limit: int = 50) -> list[dict[str, Any]]:
    """The sessions to look at for one symbol (short / excess / unknown), worst first."""
    rows = await conn.fetch(
        """
        SELECT x.session_date, x.bars, e.expected_minutes AS expected, x.first_ts, x.last_ts
        FROM app.intraday_sessions x JOIN app.instrument_ids i USING (sid)
        LEFT JOIN app.exchange_sessions e
               ON e.exchange = split_part(i.symbol, ':', 1) AND e.session_date = x.session_date
        WHERE i.symbol = $1 AND (e.expected_minutes IS NULL OR x.bars <> e.expected_minutes)
        ORDER BY abs(COALESCE(e.expected_minutes, x.bars) - x.bars) DESC, x.session_date DESC
        LIMIT $2
        """,
        symbol,
        limit,
    )
    out = []
    for r in rows:
        exp = r["expected"]
        status = "unknown" if exp is None else ("short" if r["bars"] < exp else "excess")
        out.append(
            {
                "date": r["session_date"].isoformat(),
                "bars": r["bars"],
                "expected": exp,
                "status": status,
                "first": r["first_ts"].isoformat(),
                "last": r["last_ts"].isoformat(),
            }
        )
    return out


async def intraday_report(conn: Any, now: datetime) -> dict[str, Any]:
    rows = await completeness(conn)
    size = float(await conn.fetchval("SELECT pg_database_size(current_database())")) / 1024**3
    total_rows = sum(r.bars for r in rows)
    return {
        "generated_at": now.isoformat(),
        "db_size_gb": round(size, 3),
        "summary": {
            "symbols": len(rows),
            "minute_bars": total_rows,
            "sessions": sum(r.sessions for r in rows),
            "complete_sessions": sum(r.complete for r in rows),
            "short_sessions": sum(r.short for r in rows),
            "missing_minutes": sum(r.missing_minutes for r in rows),
            "excess_sessions": sum(r.excess for r in rows),
            "unknown_sessions": sum(r.unknown for r in rows),
            "missing_sessions": sum(r.missing_sessions for r in rows),
            "oldest": min((r.first for r in rows), default=None)
            and min(r.first for r in rows).isoformat(),
            "newest": max((r.last for r in rows), default=None)
            and max(r.last for r in rows).isoformat(),
        },
        "symbols": [
            {
                **asdict(r),
                "first": r.first.isoformat(),
                "last": r.last.isoformat(),
                "complete_pct": r.complete_pct,
                "review": r.review,
            }
            for r in rows
        ],
    }
