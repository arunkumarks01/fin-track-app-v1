"""Coverage report — the Phase-1 trust gate (BUILD_PLAN; ARCHITECTURE §5).

Gate (all must hold, thresholds from config):
  1. ≥ COVERAGE_MIN_SYMBOL_PCT % of active universe symbols are *sufficient*:
     ≥ COVERAGE_MIN_CLEAN_DAILY_BARS clean daily bars AND ≤ COVERAGE_MAX_WEEKDAY_GAP_PCT of
     weekdays missing between their first and last bar (holes are not depth);
  2. every index is sufficient and its oldest bar is on/before COVERAGE_INDEX_OLDEST_BY;
  3. zero duplicate trading dates.
Quarantine counts only *unresolved* rows: a quarantined row whose trading date later got a
clean bar (e.g. re-stamped on a re-run) is history, not a hole.
An empty universe FAILS (fail closed). Per-symbol `scorable` (sufficient + fresh) is what the
later scoring pipeline will consult; staleness is reported, never hidden.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ, trading_date_of
from app.data.extract_daily import last_final_daily_session
from app.data.validate import weekdays_between


@dataclass(frozen=True)
class SymbolCoverage:
    symbol: str
    kind: str
    clean_bars: int
    oldest: date | None
    newest: date | None
    duplicate_dates: int
    quarantined: int  # unresolved
    quarantined_total: int
    flagged: int
    weekday_gap_pct: float | None  # % of weekdays listing..newest without a bar (incl. holidays)
    stale_weekdays: int | None
    sufficient: bool
    fresh: bool
    insufficient_reason: str | None = None
    listing: date | None = None  # start of the real history (ghost segments before it)
    pre_listing_bars: int = 0
    suspended_weekdays: int = 0  # long NSE no-trading gaps after listing (not missing data)

    @property
    def scorable(self) -> bool:
        return self.sufficient and self.fresh


@dataclass
class CoverageReport:
    generated_at: datetime
    as_of_session: date
    thresholds: dict[str, Any]
    symbols: list[SymbolCoverage] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def to_dict(self) -> dict[str, Any]:
        sufficient = sum(s.sufficient for s in self.symbols)
        return {
            "passed": self.passed,
            "generated_at": self.generated_at.isoformat(),
            "as_of_session": self.as_of_session.isoformat(),
            "thresholds": {k: str(v) for k, v in self.thresholds.items()},
            "checks": self.checks,
            "failures": self.failures,
            "summary": {
                "symbols": len(self.symbols),
                "sufficient": sufficient,
                "sufficient_pct": round(100 * sufficient / len(self.symbols), 1)
                if self.symbols
                else 0.0,
                "scorable": sum(s.scorable for s in self.symbols),
                "stale": sum(not s.fresh for s in self.symbols),
                "clean_bars": sum(s.clean_bars for s in self.symbols),
                "quarantined": sum(s.quarantined for s in self.symbols),
                "quarantined_total": sum(s.quarantined_total for s in self.symbols),
                "insufficient": [
                    {"symbol": s.symbol, "reason": s.insufficient_reason}
                    for s in self.symbols
                    if not s.sufficient
                ],
                "duplicate_dates": sum(s.duplicate_dates for s in self.symbols),
            },
            "symbols": [
                {
                    **asdict(s),
                    "oldest": s.oldest and s.oldest.isoformat(),
                    "listing": s.listing and s.listing.isoformat(),
                    "pre_listing_bars": s.pre_listing_bars,
                    "suspended_weekdays": s.suspended_weekdays,
                    "newest": s.newest and s.newest.isoformat(),
                    "scorable": s.scorable,
                }
                for s in self.symbols
            ],
        }


# Long gaps in a symbol's Kite series during which NSE (per the archive ledger) had at least
# $1 trading sessions but NO trading for the symbol. Such a gap is:
#   pre-listing  — if NSE never traded the symbol before it: the bars before it are ghosts
#                  (SBICARD's Jan-2015 bars vs its 2020-03-16 listing) → flagged, not counted;
#   suspension   — if NSE did trade it before (FORCEMOT 2023-10-26 → 2024-02-13): real history
#                  on both sides; the suspended weekdays are not missing data.
# Gaps the archive can't vouch for (fewer ledger sessions) are neither: they stay "missing".
_NO_TRADING_GAPS_SQL = """
WITH b AS (
    SELECT symbol, d, lag(d) OVER (PARTITION BY symbol ORDER BY d) AS prev
    FROM (SELECT symbol, (ts AT TIME ZONE 'Asia/Kolkata')::date AS d
          FROM app.ohlcv_candles c JOIN app.universe u USING (symbol)
          WHERE c.timeframe = '1d' AND u.kind = 'equity') x
)
SELECT b.symbol, b.prev, b.d,
       EXISTS (SELECT 1 FROM app.nse_bhav n
                WHERE n.symbol = b.symbol AND n.trade_date <= b.prev) AS traded_before
FROM b
WHERE b.prev IS NOT NULL
  AND b.d - b.prev > $1                         -- cheap pre-filter (calendar days)
  AND (SELECT count(*) FROM app.nse_archive_files f
        WHERE f.status = 'ok' AND f.trade_date > b.prev AND f.trade_date < b.d) >= $1
  AND NOT EXISTS (SELECT 1 FROM app.nse_bhav n
                   WHERE n.symbol = b.symbol AND n.trade_date > b.prev AND n.trade_date < b.d)
ORDER BY b.symbol, b.d
"""


async def no_trading_gaps(conn: Any, settings: Settings) -> list[Any]:
    return list(await conn.fetch(_NO_TRADING_GAPS_SQL, settings.coverage_listing_gap_sessions))


async def listing_dates(conn: Any, settings: Settings) -> dict[str, Any]:
    """Equities whose real history starts after a verified pre-listing gap (see above)."""
    out: dict[str, Any] = {}
    for g in await no_trading_gaps(conn, settings):
        if not g["traded_before"]:
            out[g["symbol"]] = max(out.get(g["symbol"], g["d"]), g["d"])
    return out


_SQL = """
SELECT u.symbol, u.kind,
       count(c.ts) FILTER (WHERE l.listing IS NULL OR c.ts >= l.ts0)    AS bars,
       count(c.ts) FILTER (WHERE c.ts < l.ts0)                         AS pre_listing,
       min(c.ts)                                                     AS first_bar,
       min(c.ts) FILTER (WHERE l.listing IS NULL OR c.ts >= l.ts0)      AS oldest,
       max(c.ts)                                                     AS newest,
       count(c.ts) - count(DISTINCT (c.ts AT TIME ZONE 'Asia/Kolkata')::date) AS dupes,
       (SELECT count(*) FROM app.ohlcv_quarantine q
         WHERE q.symbol = u.symbol AND q.timeframe = '1d'
           AND (q.ts IS NULL OR NOT EXISTS (
                SELECT 1 FROM app.ohlcv_candles c2
                WHERE c2.symbol = q.symbol AND c2.timeframe = '1d'
                  AND c2.ts = date_trunc('day', q.ts AT TIME ZONE 'Asia/Kolkata')
                              AT TIME ZONE 'Asia/Kolkata')))           AS quarantined,
       (SELECT count(*) FROM app.ohlcv_quarantine q
         WHERE q.symbol = u.symbol AND q.timeframe = '1d')            AS quarantined_total,
       (SELECT count(*) FROM app.ohlcv_flags f
         WHERE f.symbol = u.symbol AND f.timeframe = '1d')            AS flagged
FROM app.universe u
LEFT JOIN app.ohlcv_candles c ON c.symbol = u.symbol AND c.timeframe = '1d'
LEFT JOIN (SELECT symbol, listing,
                  (listing::timestamp AT TIME ZONE 'Asia/Kolkata') AS ts0
           FROM unnest($1::text[], $2::date[]) AS t(symbol, listing)) l ON l.symbol = u.symbol
WHERE u.active
GROUP BY u.symbol, u.kind
ORDER BY u.kind DESC, u.symbol
"""


async def coverage_report(conn: Any, settings: Settings, now: datetime) -> CoverageReport:
    as_of = last_final_daily_session(now, settings.daily_bar_final_next_day_ist)
    report = CoverageReport(
        generated_at=now,
        as_of_session=as_of,
        thresholds={
            "min_clean_daily_bars": settings.coverage_min_clean_daily_bars,
            "min_symbol_pct": settings.coverage_min_symbol_pct,
            "index_oldest_by": settings.coverage_index_oldest_by,
            "max_stale_weekdays": settings.coverage_max_stale_weekdays,
            "max_weekday_gap_pct": settings.coverage_max_weekday_gap_pct,
        },
    )
    gaps = await no_trading_gaps(conn, settings)
    listings: dict[str, Any] = {}
    for g in gaps:
        if not g["traded_before"]:
            listings[g["symbol"]] = max(listings.get(g["symbol"], g["d"]), g["d"])
    suspended: dict[str, int] = {}
    for g in gaps:
        start = listings.get(g["symbol"])
        if g["traded_before"] and (start is None or g["prev"] >= start):
            suspended[g["symbol"]] = suspended.get(g["symbol"], 0) + weekdays_between(
                g["prev"], g["d"]
            )
    for r in await conn.fetch(_SQL, list(listings), list(listings.values())):
        oldest = r["oldest"] and trading_date_of(r["oldest"], MARKET_TZ)
        newest = r["newest"] and trading_date_of(r["newest"], MARKET_TZ)
        gap_pct = stale = None
        if oldest and newest:
            span = weekdays_between(oldest, newest) + (2 if newest > oldest else 1)
            span -= suspended.get(r["symbol"], 0)  # NSE didn't trade: not missing data
            gap_pct = round(100 * (1 - r["bars"] / span), 2)
            stale = weekdays_between(newest, as_of) + (1 if as_of > newest else 0)
        reason = None
        if r["bars"] < settings.coverage_min_clean_daily_bars:
            reason = f"{r['bars']} clean bars < {settings.coverage_min_clean_daily_bars}"
        elif gap_pct is not None and gap_pct > settings.coverage_max_weekday_gap_pct:
            reason = f"{gap_pct}% of weekdays missing > {settings.coverage_max_weekday_gap_pct}%"
        report.symbols.append(
            SymbolCoverage(
                symbol=r["symbol"],
                kind=r["kind"],
                clean_bars=r["bars"],
                oldest=oldest,
                newest=newest,
                duplicate_dates=r["dupes"],
                quarantined=r["quarantined"],
                quarantined_total=r["quarantined_total"],
                flagged=r["flagged"],
                weekday_gap_pct=gap_pct,
                stale_weekdays=stale,
                sufficient=reason is None,
                fresh=stale is not None and stale <= settings.coverage_max_stale_weekdays,
                insufficient_reason=reason,
                listing=oldest,
                pre_listing_bars=r["pre_listing"],
                suspended_weekdays=suspended.get(r["symbol"], 0),
            )
        )

    symbols = report.symbols
    if not symbols:
        report.checks["universe_not_empty"] = False
        report.failures.append("universe is empty")
        return report
    report.checks["universe_not_empty"] = True

    pct = 100 * sum(s.sufficient for s in symbols) / len(symbols)
    report.checks["sufficient_symbols"] = pct >= settings.coverage_min_symbol_pct
    if not report.checks["sufficient_symbols"]:
        report.failures.append(
            f"only {pct:.1f}% of symbols are sufficient (≥{settings.coverage_min_clean_daily_bars} "
            f"clean bars, ≤{settings.coverage_max_weekday_gap_pct}% missing weekdays; "
            f"need {settings.coverage_min_symbol_pct}%)"
        )

    shallow = [
        s.symbol
        for s in symbols
        if s.kind == "index" and (s.oldest is None or s.oldest > settings.coverage_index_oldest_by)
    ]
    holey = [
        s.symbol
        for s in symbols
        if s.kind == "index"
        and s.oldest is not None
        and s.oldest <= settings.coverage_index_oldest_by
        and not s.sufficient
    ]
    indices = [s for s in symbols if s.kind == "index"]
    report.checks["indices_deep"] = bool(indices) and not shallow and not holey
    if not indices:
        report.failures.append("no indices in the universe")
    if shallow:
        report.failures.append(
            f"indices not back to {settings.coverage_index_oldest_by}: {shallow}"
        )
    if holey:
        report.failures.append(f"indices deep but with too many missing sessions: {holey}")

    dupes = sum(s.duplicate_dates for s in symbols)
    report.checks["zero_duplicates"] = dupes == 0
    if dupes:
        report.failures.append(f"{dupes} duplicate trading dates")
    return report


def format_report(report: CoverageReport, limit: int = 15) -> str:
    d = report.to_dict()
    s = d["summary"]
    lines = [
        f"COVERAGE GATE: {'PASS' if report.passed else 'FAIL'}   (as of session "
        f"{d['as_of_session']}, generated {d['generated_at']})",
        f"  symbols {s['symbols']}  sufficient {s['sufficient']} ({s['sufficient_pct']}%)  "
        f"scorable {s['scorable']}  stale {s['stale']}",
        f"  clean bars {s['clean_bars']}  quarantined {s['quarantined']}  "
        f"duplicate dates {s['duplicate_dates']}",
        "  checks: " + ", ".join(f"{k}={'ok' if v else 'FAIL'}" for k, v in report.checks.items()),
    ]
    lines += [f"  ✕ {f}" for f in report.failures]
    lines.append(
        f"  {'symbol':<26}{'kind':<7}{'bars':>6}  {'oldest':<11}{'newest':<11}"
        f"{'gap%':>6}{'stale':>6}{'quar':>5}  ok"
    )
    for c in report.symbols[:limit]:
        lines.append(
            f"  {c.symbol:<26}{c.kind:<7}{c.clean_bars:>6}  {c.oldest or '-'!s:<11}"
            f"{c.newest or '-'!s:<11}{c.weekday_gap_pct or '-'!s:>6}"
            f"{c.stale_weekdays if c.stale_weekdays is not None else '-'!s:>6}"
            f"{c.quarantined:>5}  {'✓' if c.scorable else '✕'}"
        )
    if len(report.symbols) > limit:
        lines.append(f"  … {len(report.symbols) - limit} more")
    return "\n".join(lines)
