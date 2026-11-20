"""Load NSE's daily PR archive (corporate actions + official as-traded prices). Phase 1c.

Resumable: one transaction per trading date writes its actions, prices and ledger row
together, so an interrupted run never leaves a half-loaded day and a re-run skips every date
already 'ok'. Dates come from the stored NSE benchmark's daily bars (real sessions only).
Newest first, so the most useful data lands first. A 404 is recorded as 'missing' (an
archive gap, reported); other failures as 'error' (retried next run). A 403 stops the run:
the archive refused us, and we never retry with different headers.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.core.clock import Clock
from app.core.config import Settings
from app.data.nse_archive import (
    NseArchiveClient,
    NseArchiveError,
    NseArchiveMislabeled,
    NseArchiveRefused,
    SymbolChange,
    names_on,
    parse_bc,
    parse_pd,
    parse_symbol_changes,
    read_members,
)

log = logging.getLogger(__name__)


# Raise when parsing changes what a day yields: every older 'ok' day is re-fetched once and
# only missing rows are added. v2 (2026-11-20): rename mapping + BE/BZ fallback.
PARSE_VERSION = 2


@dataclass
class NseLoadReport:
    started_at: datetime
    dates_todo: int = 0
    done: int = 0
    ok: int = 0
    missing: int = 0
    errors: int = 0
    actions: int = 0
    bhav_rows: int = 0
    rejected: int = 0
    symbol_changes: int = 0
    stopped_reason: str | None = None

    def totals(self) -> dict[str, Any]:
        return {
            "dates_todo": self.dates_todo,
            "done": self.done,
            "ok": self.ok,
            "missing": self.missing,
            "errors": self.errors,
            "actions": self.actions,
            "bhav_rows": self.bhav_rows,
            "rejected": self.rejected,
            "symbol_changes": self.symbol_changes,
            # shape shared with the other jobs' totals (Admin page)
            "inserted": self.bhav_rows,
            "quarantined": self.rejected,
        }


async def dates_to_load(
    conn: Any, settings: Settings, last_final: date, retry_missing: bool = False
) -> list[date]:
    benchmark = settings.intraday_benchmarks.get("NSE")
    if benchmark is None:
        raise ValueError("no NSE benchmark configured (INTRADAY_BENCHMARKS)")
    skip = ["ok"] if retry_missing else ["ok", "missing"]
    rows = await conn.fetch(
        """
        SELECT (c.ts AT TIME ZONE 'Asia/Kolkata')::date AS d
        FROM app.ohlcv_candles c
        WHERE c.symbol = $1 AND c.timeframe = '1d'
          AND (c.ts AT TIME ZONE 'Asia/Kolkata')::date BETWEEN $2 AND $3
          AND NOT EXISTS (
              SELECT 1 FROM app.nse_archive_files f
              WHERE f.trade_date = (c.ts AT TIME ZONE 'Asia/Kolkata')::date
                AND f.status = ANY($4::text[])
                AND NOT (f.status = 'ok' AND f.parse_version < $5))
        ORDER BY 1 DESC
        """,
        benchmark,
        settings.nse_archive_start,
        last_final,
        skip,
        PARSE_VERSION,
    )
    return [r["d"] for r in rows]


async def nse_universe(conn: Any) -> set[str]:
    rows = await conn.fetch(
        "SELECT symbol FROM app.universe WHERE kind = 'equity' AND symbol LIKE 'NSE:%'"
    )
    return {r["symbol"] for r in rows}


async def _ledger(
    conn: Any,
    d: date,
    status: str,
    now: datetime,
    detail: str | None,
    bc: int = 0,
    pd: int = 0,
    rejected: int = 0,
    parse_version: int = PARSE_VERSION,
) -> None:
    await conn.execute(
        """INSERT INTO app.nse_archive_files
               (trade_date, status, bc_rows, pd_rows, rejected, detail, fetched_at,
                parse_version)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           ON CONFLICT (trade_date) DO UPDATE SET status = EXCLUDED.status,
               bc_rows = EXCLUDED.bc_rows, pd_rows = EXCLUDED.pd_rows,
               rejected = EXCLUDED.rejected, detail = EXCLUDED.detail,
               fetched_at = EXCLUDED.fetched_at, parse_version = EXCLUDED.parse_version""",
        d,
        status,
        bc,
        pd,
        rejected,
        detail and detail[:500],
        now,
        parse_version,
    )


def _renamed(symbol: str, source: str) -> str | None:
    """NSE's name that day, only when it differs from our current symbol."""
    return source if source and source != symbol.split(":", 1)[1] else None


async def refresh_symbol_changes(
    conn: Any, client: NseArchiveClient, settings: Settings, report: NseLoadReport
) -> list[SymbolChange]:
    """NSE's symbol-change list: refreshed from the archive each run; on a fetch failure the
    stored copy is used (and the failure logged). Never an empty map by accident."""
    try:
        rows = parse_symbol_changes(await client.fetch_text(settings.nse_symbol_changes_url))
    except NseArchiveRefused:
        raise  # a refusal stops the load — never worked around, not even with a stored copy
    except NseArchiveError as exc:
        log.warning("symbol changes not refreshed (%s); using the stored list", exc)
    else:
        if rows:
            await conn.executemany(
                """INSERT INTO app.symbol_changes (old_symbol, new_symbol, effective_date, company)
                   VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING""",
                [(r.old, r.new, r.effective, r.company) for r in rows],
            )
    stored = [
        SymbolChange(r["old_symbol"], r["new_symbol"], r["effective_date"], r["company"])
        for r in await conn.fetch("SELECT * FROM app.symbol_changes")
    ]
    report.symbol_changes = len(stored)
    return stored


async def store_day(
    conn: Any,
    d: date,
    blob: bytes,
    universe: set[str],
    settings: Settings,
    now: datetime,
    changes: Sequence[SymbolChange] = (),
) -> tuple[int, int, int]:
    """Parse one zip and store it (caller holds the transaction). Names are resolved to the
    symbol each company traded under ON `d` (renames). Only missing rows are inserted.
    Returns (actions, bhav, rejected)."""
    bc_text, pd_text = read_members(
        blob, int(settings.nse_archive_max_member_mb * 1024 * 1024), expected=d
    )
    names = names_on(d, universe, changes)
    bc = parse_bc(bc_text, names)
    pd = parse_pd(pd_text, d, names)
    if bc.rows:
        await conn.executemany(
            """INSERT INTO app.corporate_actions
                   (symbol, series, ex_date, record_date, purpose, kinds, split_from_paisa,
                    split_to_paisa, bonus_new, bonus_held, dividend_paisa, first_seen,
                    source_symbol)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
               ON CONFLICT (symbol, series, ex_date, purpose)
               DO UPDATE SET first_seen = LEAST(app.corporate_actions.first_seen,
                                                EXCLUDED.first_seen)""",
            [
                (
                    a.symbol,
                    a.series,
                    a.ex_date,
                    a.record_date,
                    a.purpose,
                    list(a.kinds),
                    a.split_from_paisa,
                    a.split_to_paisa,
                    a.bonus_new,
                    a.bonus_held,
                    a.dividend_paisa,
                    d,
                    _renamed(a.symbol, a.source_symbol),
                )
                for a in bc.rows
            ],
        )
    if pd.rows:
        await conn.executemany(
            """INSERT INTO app.nse_bhav
                   (symbol, trade_date, prev_close, open, high, low, close, volume, series,
                    source_symbol)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
               ON CONFLICT (symbol, trade_date) DO NOTHING""",
            [
                (
                    b.symbol,
                    b.trade_date,
                    b.prev_close,
                    b.open,
                    b.high,
                    b.low,
                    b.close,
                    b.volume,
                    b.series,
                    _renamed(b.symbol, b.source_symbol),
                )
                for b in pd.rows
            ],
        )
    rejected = bc.rejected + pd.rejected
    detail = json.dumps({"bc_rejected": bc.rejected, "pd_rejected": pd.rejected})
    await _ledger(conn, d, "ok", now, detail, len(bc.rows), len(pd.rows), rejected)
    return len(bc.rows), len(pd.rows), rejected


async def run_nse_archive_load(
    pool: Any,
    client: NseArchiveClient,
    settings: Settings,
    clock: Clock,
    last_final: date,
    progress: Callable[[NseLoadReport], None] | None = None,
    retry_missing: bool = False,
) -> NseLoadReport:
    report = NseLoadReport(started_at=clock.now())
    async with pool.acquire() as conn:
        todo = await dates_to_load(conn, settings, last_final, retry_missing)
        universe = await nse_universe(conn)
        try:
            changes = await refresh_symbol_changes(conn, client, settings, report)
        except NseArchiveRefused as exc:
            report.stopped_reason = str(exc)
            return report
        if not changes:
            report.stopped_reason = (
                "no NSE symbol-change list (fetch failed and none stored) — renamed symbols "
                "would be mis-mapped; not loading"
            )
            return report
    report.dates_todo = len(todo)
    if not universe:
        report.stopped_reason = "universe is empty — refresh the universe first"
        return report
    for d in todo:
        try:
            blob = await client.fetch_zip(d)
        except NseArchiveRefused as exc:
            report.stopped_reason = str(exc)
            log.error("nse archive: %s — stopping", exc)
            break
        except NseArchiveError as exc:
            report.errors += 1
            async with pool.acquire() as conn:
                await _ledger(conn, d, "error", clock.now(), str(exc))
            log.warning("nse archive %s: %s", d, exc)
        else:
            async with pool.acquire() as conn, conn.transaction():
                if blob is None:
                    report.missing += 1
                    await _ledger(conn, d, "missing", clock.now(), "404 from the archive")
                else:
                    try:
                        a, b, r = await store_day(
                            conn, d, blob, universe, settings, clock.now(), changes
                        )
                    except NseArchiveMislabeled as exc:  # no correct file exists: a gap
                        report.missing += 1
                        await _ledger(conn, d, "missing", clock.now(), str(exc))
                        log.warning("nse archive %s: %s — recorded as a gap", d, exc)
                    except NseArchiveError as exc:  # bad zip/member: recorded, not fatal
                        report.errors += 1
                        await _ledger(conn, d, "error", clock.now(), str(exc))
                    else:
                        report.ok += 1
                        report.actions += a
                        report.bhav_rows += b
                        report.rejected += r
        report.done += 1
        if progress:
            progress(report)
    log.info("nse archive load: %s", report.totals())
    return report


async def reset_archive_day(conn: Any, d: date, purge: bool, reason: str, now: datetime) -> int:
    """Queue one archive day for exactly one re-fetch (ledger → 'error'). With `purge`, its
    stored official rows are deleted first — each one logged to app.data_repairs with its old
    values and the reason (the audit trail). Returns the number of rows deleted.
    Caller holds the transaction."""
    deleted = 0
    if purge:
        rows = await conn.fetch("SELECT * FROM app.nse_bhav WHERE trade_date = $1", d)
        if rows:
            await conn.executemany(
                """INSERT INTO app.data_repairs
                   (symbol, timeframe, ts, old_values, new_values, reason, repaired_at)
                   VALUES ($1, 'nse_bhav', ($2::timestamp AT TIME ZONE 'Asia/Kolkata'),
                           $3::jsonb, '{"deleted": true}'::jsonb, $4, $5)""",
                [
                    (
                        r["symbol"],
                        d,
                        json.dumps(
                            {
                                k: r[k]
                                for k in ("prev_close", "open", "high", "low", "close", "volume")
                            }
                        ),
                        reason,
                        now,
                    )
                    for r in rows
                ],
            )
            await conn.execute("DELETE FROM app.nse_bhav WHERE trade_date = $1", d)
            deleted = len(rows)
    await _ledger(conn, d, "error", now, f"reset for one re-fetch: {reason}")
    return deleted
