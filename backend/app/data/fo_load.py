"""Load the F&O bhavcopy history (slice 1f) from NSE's archive.

Same rules as the 1c and 1e archive loads: dates are the NSE benchmark's stored sessions,
newest first; one transaction per day writes its rows and its ledger row together; a re-run
skips every day already 'ok'; a 404 or a mislabelled file is a recorded gap; other failures are
'error' and retried; a 403 stops the run (never retried with other headers). Extra rule: this is
the biggest table, so the load stops once the database reaches FO_DB_BUDGET_GB.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.core.clock import Clock
from app.core.config import Settings
from app.data.extract_intraday import db_size_gb
from app.data.nse_archive import (
    NseArchiveClient,
    NseArchiveError,
    NseArchiveMislabeled,
    NseArchiveRefused,
)
from app.data.nse_fo import FO_DATASET, fo_url, parse_fo, read_csv
from app.data.reference_load import _ledger, dates_to_load

log = logging.getLogger(__name__)

BUDGET_CHECK_EVERY = 20  # days between database-size checks


@dataclass
class FoReport:
    started_at: datetime
    todo: int = 0
    done: int = 0
    ok: int = 0
    missing: int = 0
    errors: int = 0
    rows: int = 0
    idle: int = 0
    odd_range: int = 0
    rejected: int = 0
    db_size_gb: float | None = None
    stopped_reason: str | None = None

    def totals(self) -> dict[str, Any]:
        return {
            "todo": self.todo,
            "done": self.done,
            "ok": self.ok,
            "missing": self.missing,
            "errors": self.errors,
            "rows": self.rows,
            "idle_skipped": self.idle,
            "odd_range": self.odd_range,
            "rejected": self.rejected,
            "db_size_gb": self.db_size_gb,
            # shape shared with the other jobs' totals (Admin page)
            "inserted": self.rows,
            "quarantined": self.rejected,
        }


async def store_fo_day(
    conn: Any, d: date, blob: bytes, settings: Settings, now: datetime
) -> tuple[int, int, int, int]:
    """Parse and store one day (caller holds the transaction): (rows, idle, odd, rejected)."""
    f = parse_fo(read_csv(blob, int(settings.nse_archive_max_member_mb * 1024 * 1024) * 4), d)
    if f.rows:
        await conn.executemany(
            """INSERT INTO app.fo_daily
                   (trade_date, underlying, kind, expiry, strike_paisa, opt_type, open_paisa,
                    high_paisa, low_paisa, close_paisa, settle_paisa, prev_close_paisa,
                    underlying_paisa, contracts, open_interest, oi_change, lot_size)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
               ON CONFLICT DO NOTHING""",
            [
                (
                    r.trade_date,
                    r.underlying,
                    r.kind,
                    r.expiry,
                    r.strike_paisa,
                    r.opt_type,
                    r.open,
                    r.high,
                    r.low,
                    r.close,
                    r.settle,
                    r.prev_close,
                    r.underlying_price,
                    r.contracts,
                    r.open_interest,
                    r.oi_change,
                    r.lot_size,
                )
                for r in f.rows
            ],
        )
    detail = json.dumps({"idle_skipped": f.idle, "odd_range": f.odd_range, "rejected": f.rejected})
    await _ledger(conn, FO_DATASET, d, "ok", now, detail, len(f.rows), f.rejected)
    return len(f.rows), f.idle, f.odd_range, f.rejected


async def run_fo_load(
    pool: Any,
    client: NseArchiveClient,
    settings: Settings,
    clock: Clock,
    last_final: date,
    progress: Callable[[FoReport], None] | None = None,
    retry_missing: bool = False,
) -> FoReport:
    report = FoReport(started_at=clock.now())
    async with pool.acquire() as conn:
        todo = await dates_to_load(conn, settings, FO_DATASET, last_final, retry_missing)
        report.db_size_gb = await db_size_gb(conn)
    report.todo = len(todo)
    for n, d in enumerate(todo):
        if n and n % BUDGET_CHECK_EVERY == 0:
            async with pool.acquire() as conn:
                report.db_size_gb = await db_size_gb(conn)
        if report.db_size_gb is not None and report.db_size_gb >= settings.fo_db_budget_gb:
            report.stopped_reason = (
                f"database is {report.db_size_gb:.2f} GB, at the {settings.fo_db_budget_gb:.2f} GB "
                "budget (FO_DB_BUDGET_GB) — F&O history stopped"
            )
            break
        url = fo_url(
            settings.nse_fo_old_base_url, settings.nse_fo_new_base_url, settings.fo_udiff_from, d
        )
        try:
            blob = await client.fetch_file(url)
        except NseArchiveRefused as exc:
            report.stopped_reason = str(exc)
            log.error("fo load: %s — stopping", exc)
            break
        except NseArchiveError as exc:
            report.errors += 1
            async with pool.acquire() as conn:
                await _ledger(conn, FO_DATASET, d, "error", clock.now(), str(exc))
            log.warning("fo %s: %s", d, exc)
        else:
            async with pool.acquire() as conn, conn.transaction():
                if blob is None:
                    report.missing += 1
                    await _ledger(
                        conn, FO_DATASET, d, "missing", clock.now(), "404 from the archive"
                    )
                else:
                    try:
                        rows, idle, odd, rej = await store_fo_day(
                            conn, d, blob, settings, clock.now()
                        )
                    except NseArchiveMislabeled as exc:
                        report.missing += 1
                        await _ledger(conn, FO_DATASET, d, "missing", clock.now(), str(exc))
                    except NseArchiveError as exc:
                        report.errors += 1
                        await _ledger(conn, FO_DATASET, d, "error", clock.now(), str(exc))
                    else:
                        report.ok += 1
                        report.rows += rows
                        report.idle += idle
                        report.odd_range += odd
                        report.rejected += rej
        report.done += 1
        if progress:
            progress(report)
    log.info("fo load: %s", report.totals())
    return report
