"""Calendar-aware 1-minute completeness — PER INSTRUMENT (revised DEC-1B, 2026-11-20).

Each session is judged against the instrument's OWN norm (app.intraday_session_status, kept
by app/data/session_calendar.py): never a hardcoded 375 and never a cross-sectional
benchmark or percentile. Per session:
  complete      bars == the instrument's norm that session
  short         bars <  norm → flagged for review
  excess        bars >  norm → flagged for review
  market_short  short on a market-wide short day (outage / muhurat / half-day) — reported,
                not flagged
  unknown       no norm yet (too few sessions) → not guessed
Plus sessions inside a symbol's span with no bars at all, split in two:
  not_traded    an equity NSE didn't trade that day (the archive loaded; no official row) —
                a suspension, not a data gap; reported, never flagged
  missing       everything else (NSE traded it, the archive can't vouch, or an index)
Nothing here quarantines data — it reports, and later stages decide what is usable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

_SQL = """
WITH s AS (
    SELECT i.symbol, x.sid, x.session_date, x.bars, st.expected,
           COALESCE(st.status, 'unknown') AS status
    FROM app.intraday_sessions x
    JOIN app.instrument_ids i USING (sid)
    LEFT JOIN app.intraday_session_status st
           ON st.sid = x.sid AND st.session_date = x.session_date
),
per AS (
    SELECT symbol, min(sid) AS sid, min(session_date) AS first_d, max(session_date) AS last_d,
           count(*) AS sessions,
           sum(bars) AS bars,
           count(*) FILTER (WHERE status = 'complete') AS complete,
           count(*) FILTER (WHERE status = 'short') AS short,
           COALESCE(sum(expected - bars) FILTER (WHERE status = 'short'), 0) AS missing_minutes,
           count(*) FILTER (WHERE status = 'excess') AS excess,
           count(*) FILTER (WHERE status = 'market_short') AS market_short,
           count(*) FILTER (WHERE status = 'unknown') AS unknown
    FROM s GROUP BY symbol
),
-- exchange sessions inside the symbol's span with no bars at all ((sid, date) is unique)
absent AS (
    SELECT p.symbol, e.session_date
    FROM per p
    JOIN app.exchange_sessions e
      ON e.exchange = split_part(p.symbol, ':', 1)
     AND e.session_date BETWEEN p.first_d AND p.last_d
    WHERE NOT EXISTS (SELECT 1 FROM app.intraday_sessions x
                       WHERE x.sid = p.sid AND x.session_date = e.session_date)
),
gaps AS (
    SELECT a.symbol, count(*) AS absent,
           count(*) FILTER (
               WHERE u.kind = 'equity'
                 AND EXISTS (SELECT 1 FROM app.nse_archive_files f
                              WHERE f.trade_date = a.session_date AND f.status = 'ok')
                 AND NOT EXISTS (SELECT 1 FROM app.nse_bhav n
                                  WHERE n.symbol = a.symbol AND n.trade_date = a.session_date)
           ) AS not_traded
    FROM absent a JOIN app.universe u USING (symbol)
    GROUP BY a.symbol
)
SELECT p.symbol, p.first_d, p.last_d, p.sessions, p.bars, p.complete, p.short,
       p.missing_minutes, p.excess, p.market_short, p.unknown,
       COALESCE(g.absent - g.not_traded, 0) AS missing_sessions,
       COALESCE(g.not_traded, 0) AS not_traded_sessions
FROM per p LEFT JOIN gaps g USING (symbol)
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
    market_short: int = 0
    not_traded_sessions: int = 0  # NSE didn't trade the equity (suspension): not a data gap

    @property
    def complete_pct(self) -> float:
        judged = self.sessions - self.unknown - self.market_short
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
            r["market_short"],
            r["not_traded_sessions"],
        )
        for r in await conn.fetch(_SQL)
    ]


async def review_sessions(conn: Any, symbol: str, limit: int = 50) -> list[dict[str, Any]]:
    """The sessions to look at for one symbol (short / excess / unknown), worst first."""
    rows = await conn.fetch(
        """
        SELECT x.session_date, x.bars, st.expected, COALESCE(st.status, 'unknown') AS status,
               x.first_ts, x.last_ts
        FROM app.intraday_sessions x JOIN app.instrument_ids i USING (sid)
        LEFT JOIN app.intraday_session_status st
               ON st.sid = x.sid AND st.session_date = x.session_date
        WHERE i.symbol = $1 AND COALESCE(st.status, 'unknown') IN ('short', 'excess', 'unknown')
        ORDER BY abs(COALESCE(st.expected, x.bars) - x.bars) DESC, x.session_date DESC
        LIMIT $2
        """,
        symbol,
        limit,
    )
    return [
        {
            "date": r["session_date"].isoformat(),
            "bars": r["bars"],
            "expected": r["expected"],
            "status": r["status"],
            "first": r["first_ts"].isoformat(),
            "last": r["last_ts"].isoformat(),
        }
        for r in rows
    ]


async def intraday_report(
    conn: Any, now: datetime, analysis_start: date | None = None
) -> dict[str, Any]:
    """analysis_start: INTRADAY_ANALYSIS_START (DEC-2015), reported so the default is visible."""
    rows = await completeness(conn)
    size = float(await conn.fetchval("SELECT pg_database_size(current_database())")) / 1024**3
    total_rows = sum(r.bars for r in rows)
    return {
        "generated_at": now.isoformat(),
        "db_size_gb": round(size, 3),
        "analysis_start": analysis_start.isoformat() if analysis_start else None,
        "summary": {
            "symbols": len(rows),
            "minute_bars": total_rows,
            "sessions": sum(r.sessions for r in rows),
            "complete_sessions": sum(r.complete for r in rows),
            "short_sessions": sum(r.short for r in rows),
            "missing_minutes": sum(r.missing_minutes for r in rows),
            "excess_sessions": sum(r.excess for r in rows),
            "market_short_sessions": sum(r.market_short for r in rows),
            "unknown_sessions": sum(r.unknown for r in rows),
            "missing_sessions": sum(r.missing_sessions for r in rows),
            "not_traded_sessions": sum(r.not_traded_sessions for r in rows),
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
