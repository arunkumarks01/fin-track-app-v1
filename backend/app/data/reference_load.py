"""Load Phase 1e reference data from NSE's archive.

Same model as the 1c archive loader (nse_load.py):
  - dates are the NSE benchmark's stored daily sessions from NSE_ARCHIVE_START, newest first;
  - one transaction per (dataset, date) writes the rows and its ledger row together, so an
    interrupted run never leaves half a day, and a re-run skips every date already 'ok';
  - 404 → 'missing' (a reported gap, never back-filled from elsewhere); a mislabelled file →
    'missing'; other failures → 'error' (retried next run); a 403 stops the whole run.
Names are resolved per day through NSE's symbol-change list (stored by the 1c load).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
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
    archive_url,
    names_on,
)
from app.data.nse_load import nse_universe
from app.data.nse_reference import (
    DATASETS,
    DATED_DATASETS,
    DEAL_DATASETS,
    FIELDS,
    MARKET_CAP_DATASET,
    MARKET_CAP_START,
    MTO_DATASET,
    PARTICIPANT_DATASETS,
    PR_DATASETS,
    PR_TEXT_DATASET,
    deals_url,
    mto_url,
    parse_announcements,
    parse_board_meetings,
    parse_deals,
    parse_market_cap,
    parse_mto,
    parse_participants,
    participant_url,
    read_pr_members,
)

log = logging.getLogger(__name__)

PARSE_VERSION = 1


@dataclass
class ReferenceReport:
    started_at: datetime
    todo: dict[str, int] = field(default_factory=dict)
    done: int = 0
    ok: int = 0
    missing: int = 0
    errors: int = 0
    delivery_rows: int = 0
    participant_rows: int = 0
    rejected: int = 0
    rounding_days: int = 0  # participant files whose totals differ by ≤ tolerance (recorded)
    deals: int = 0
    announcements: int = 0
    board_meetings: int = 0
    market_cap_rows: int = 0
    stopped_reason: str | None = None

    def totals(self) -> dict[str, Any]:
        return {
            "todo": self.todo,
            "done": self.done,
            "ok": self.ok,
            "missing": self.missing,
            "errors": self.errors,
            "delivery_rows": self.delivery_rows,
            "participant_rows": self.participant_rows,
            "rejected": self.rejected,
            "rounding_days": self.rounding_days,
            "deals": self.deals,
            "announcements": self.announcements,
            "board_meetings": self.board_meetings,
            "market_cap_rows": self.market_cap_rows,
            # shape shared with the other jobs' totals (Admin page)
            "inserted": self.delivery_rows
            + self.participant_rows
            + self.deals
            + self.announcements
            + self.board_meetings
            + self.market_cap_rows,
            "quarantined": self.rejected,
        }


async def dates_to_load(
    conn: Any, settings: Settings, dataset: str, last_final: date, retry_missing: bool = False
) -> list[date]:
    benchmark = settings.intraday_benchmarks.get("NSE")
    if benchmark is None:
        raise ValueError("no NSE benchmark configured (INTRADAY_BENCHMARKS)")
    skip = ["ok"] if retry_missing else ["ok", "missing"]
    start = settings.nse_archive_start
    if dataset == MARKET_CAP_DATASET:
        start = max(start, MARKET_CAP_START)
    rows = await conn.fetch(
        """
        SELECT (c.ts AT TIME ZONE 'Asia/Kolkata')::date AS d
        FROM app.ohlcv_candles c
        WHERE c.symbol = $1 AND c.timeframe = '1d'
          AND (c.ts AT TIME ZONE 'Asia/Kolkata')::date BETWEEN $2 AND $3
          AND NOT EXISTS (
              SELECT 1 FROM app.reference_files f
              WHERE f.dataset = $4
                AND f.trade_date = (c.ts AT TIME ZONE 'Asia/Kolkata')::date
                AND f.status = ANY($5::text[])
                AND NOT (f.status = 'ok' AND f.parse_version < $6))
        ORDER BY 1 DESC
        """,
        benchmark,
        start,
        last_final,
        dataset,
        skip,
        PARSE_VERSION,
    )
    return [r["d"] for r in rows]


async def _ledger(
    conn: Any,
    dataset: str,
    d: date,
    status: str,
    now: datetime,
    detail: str | None,
    rows: int = 0,
    rejected: int = 0,
) -> None:
    await conn.execute(
        """INSERT INTO app.reference_files
               (dataset, trade_date, status, rows_stored, rejected, detail, fetched_at,
                parse_version)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           ON CONFLICT (dataset, trade_date) DO UPDATE SET status = EXCLUDED.status,
               rows_stored = EXCLUDED.rows_stored, rejected = EXCLUDED.rejected,
               detail = EXCLUDED.detail, fetched_at = EXCLUDED.fetched_at,
               parse_version = EXCLUDED.parse_version""",
        dataset,
        d,
        status,
        rows,
        rejected,
        detail and detail[:500],
        now,
        PARSE_VERSION,
    )


async def store_mto(
    conn: Any, d: date, blob: bytes, names: dict[str, str], now: datetime
) -> tuple[int, int]:
    parsed = parse_mto(blob.decode("utf-8", errors="replace"), d, names)
    if parsed.rows:
        await conn.executemany(
            """INSERT INTO app.delivery_daily
                   (symbol, trade_date, series, traded_qty, deliverable_qty, deliv_bps,
                    source_symbol)
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               ON CONFLICT (symbol, trade_date) DO NOTHING""",
            [
                (
                    r.symbol,
                    r.trade_date,
                    r.series,
                    r.traded_qty,
                    r.deliverable_qty,
                    r.deliv_bps,
                    r.source_symbol if r.source_symbol != r.symbol.split(":", 1)[1] else None,
                )
                for r in parsed.rows
            ],
        )
    detail = json.dumps({"rejected": parsed.rejected}) if parsed.rejected else None
    await _ledger(conn, MTO_DATASET, d, "ok", now, detail, len(parsed.rows), parsed.rejected)
    return len(parsed.rows), parsed.rejected


async def store_participants(
    conn: Any, dataset: str, d: date, blob: bytes, tolerance: int, now: datetime
) -> tuple[int, int]:
    kind = PARTICIPANT_DATASETS[dataset]
    parsed = parse_participants(blob.decode("utf-8", errors="replace"), d, kind, tolerance)
    cols = ", ".join(FIELDS)
    marks = ", ".join(f"${i}" for i in range(4, 4 + len(FIELDS)))
    await conn.executemany(
        f"""INSERT INTO app.participant_positions (trade_date, kind, client_type, {cols})
            VALUES ($1, $2, $3, {marks})
            ON CONFLICT (trade_date, kind, client_type) DO NOTHING""",  # noqa: S608 — fixed names
        [(r.trade_date, r.kind, r.client_type, *r.values) for r in parsed.rows],
    )
    detail = json.dumps({"nse_total_gap_contracts": parsed.total_gap}) if parsed.total_gap else None
    await _ledger(conn, dataset, d, "ok", now, detail, len(parsed.rows))
    return len(parsed.rows), parsed.total_gap


async def store_pr_text(
    conn: Any, d: date, blob: bytes, names: dict[str, str], settings: Settings, now: datetime
) -> tuple[int, int, int]:
    members = read_pr_members(
        blob, int(settings.nse_archive_max_member_mb * 1024 * 1024), d, PR_TEXT_DATASET
    )
    an_text, bm_text = members.get("an"), members.get("bm")
    an = parse_announcements(an_text, d, names) if an_text else None
    bm = parse_board_meetings(bm_text, d, names) if bm_text else None
    if an and an.rows:
        await conn.executemany(
            """INSERT INTO app.announcements (symbol, announced_on, category, body, source_symbol)
               VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING""",
            [
                (a.symbol, a.announced_on, a.category, a.body, _renamed(a.symbol, a.source_symbol))
                for a in an.rows
            ],
        )
    if bm and bm.rows:
        await conn.executemany(
            """INSERT INTO app.board_meetings
                   (symbol, meeting_date, purpose, is_results, first_seen, source_symbol)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (symbol, meeting_date, md5(purpose))
               DO UPDATE SET first_seen = LEAST(app.board_meetings.first_seen,
                                                EXCLUDED.first_seen)""",
            [
                (
                    b.symbol,
                    b.meeting_date,
                    b.purpose,
                    b.is_results,
                    b.first_seen,
                    _renamed(b.symbol, b.source_symbol),
                )
                for b in bm.rows
            ],
        )
    n_an, n_bm = (len(an.rows) if an else 0), (len(bm.rows) if bm else 0)
    rejected = (an.rejected if an else 0) + (bm.rejected if bm else 0)
    detail = json.dumps(
        {
            "announcements": n_an,
            "board_meetings": n_bm,
            "rejected": rejected,
            "members": [k for k, t in (("an", an_text), ("bm", bm_text)) if t],
        }
    )
    await _ledger(conn, PR_TEXT_DATASET, d, "ok", now, detail, n_an + n_bm, rejected)
    return n_an, n_bm, rejected


async def store_market_cap(
    conn: Any, d: date, blob: bytes, names: dict[str, str], settings: Settings, now: datetime
) -> tuple[int, int]:
    members = read_pr_members(
        blob, int(settings.nse_archive_max_member_mb * 1024 * 1024), d, MARKET_CAP_DATASET
    )
    if "mcap" not in members:
        raise NseArchiveMislabeled("PR zip lacks the market-cap member")
    parsed = parse_market_cap(members["mcap"], d, names)
    await conn.executemany(
        """INSERT INTO app.market_cap_daily
               (symbol, trade_date, series, issue_size, market_cap_paisa, source_symbol)
           VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING""",
        [
            (
                r.symbol,
                d,
                r.series,
                r.issue_size,
                r.market_cap_paisa,
                _renamed(r.symbol, r.source_symbol),
            )
            for r in parsed.rows
        ],
    )
    await _ledger(conn, MARKET_CAP_DATASET, d, "ok", now, None, len(parsed.rows), parsed.rejected)
    return len(parsed.rows), parsed.rejected


def _renamed(symbol: str, source: str) -> str | None:
    return source if source and source != symbol.split(":", 1)[1] else None


async def _record_skipped_deal_sessions(
    conn: Any, settings: Settings, dataset: str, last_final: date, now: datetime
) -> int:
    benchmark = settings.intraday_benchmarks.get("NSE")
    if benchmark is None:
        raise ValueError("no NSE benchmark configured (INTRADAY_BENCHMARKS)")
    rows = await conn.fetch(
        """INSERT INTO app.reference_files AS f
               (dataset, trade_date, status, detail, fetched_at, parse_version)
           SELECT $1, (c.ts AT TIME ZONE 'Asia/Kolkata')::date, 'missing',
                  'This session was never captured', $4, $5
           FROM app.ohlcv_candles c
           WHERE c.symbol = $2 AND c.timeframe = '1d'
             AND c.ts >= (SELECT min(trade_date)::timestamp AT TIME ZONE 'Asia/Kolkata'
                          FROM app.reference_files WHERE dataset = $1)
             AND c.ts < ($3::date::timestamp AT TIME ZONE 'Asia/Kolkata')
           ON CONFLICT (dataset, trade_date) DO UPDATE
               SET status = EXCLUDED.status, detail = EXCLUDED.detail,
                   fetched_at = EXCLUDED.fetched_at, parse_version = EXCLUDED.parse_version
               WHERE f.status = 'error'
           RETURNING trade_date""",
        dataset,
        benchmark,
        last_final,
        now,
        PARSE_VERSION,
    )
    return len(rows)


async def load_deals(
    pool: Any,
    client: NseArchiveClient,
    settings: Settings,
    clock: Clock,
    last_final: date,
    report: ReferenceReport,
    datasets: Sequence[str],
) -> None:
    """Bulk/block deal files hold the LATEST session only: store it once; if the newest final
    session isn't what the file shows (and was never captured), record that day as missing —
    it can never be fetched later."""
    for ds in datasets:
        if ds not in DEAL_DATASETS:
            continue
        kind = DEAL_DATASETS[ds]
        blob = await client.fetch_file(deals_url(settings.nse_deals_base_url, ds))
        async with pool.acquire() as conn, conn.transaction():
            have = {
                r["trade_date"]: r["status"]
                for r in await conn.fetch(
                    "SELECT trade_date, status FROM app.reference_files WHERE dataset = $1", ds
                )
            }
            if blob is None:
                if have.get(last_final) != "ok":
                    report.missing += 1
                    await _ledger(conn, ds, last_final, "missing", clock.now(), "404 from NSE")
                report.missing += await _record_skipped_deal_sessions(
                    conn, settings, ds, last_final, clock.now()
                )
                continue
            try:
                f = parse_deals(blob.decode("utf-8", errors="replace"), kind)
            except NseArchiveError as exc:
                report.errors += 1
                await _ledger(conn, ds, last_final, "error", clock.now(), str(exc))
                continue
            shown = f.trade_date or last_final  # a header-only file: no deals that session
            if have.get(shown) != "ok" and shown <= last_final:
                if f.rows:
                    await conn.executemany(
                        """INSERT INTO app.deals (trade_date, kind, symbol, security_name, client,
                               side, quantity, price_paisa, price_exact, remarks)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                           ON CONFLICT DO NOTHING""",
                        [
                            (
                                r.trade_date,
                                r.kind,
                                r.symbol,
                                r.security_name,
                                r.client,
                                r.side,
                                r.quantity,
                                r.price_paisa,
                                r.price_exact,
                                r.remarks,
                            )
                            for r in f.rows
                        ],
                    )
                # a header-only file states no date: it can only mean "no deals in the latest
                # session", which is the newest final one when the job runs on time
                detail = None if f.rows else "no deals published (empty file; date not stated)"
                await _ledger(conn, ds, shown, "ok", clock.now(), detail, len(f.rows), f.rejected)
                report.ok += 1
                report.deals += len(f.rows)
                report.rejected += f.rejected
            if shown != last_final and have.get(last_final) != "ok":
                report.missing += 1
                await _ledger(
                    conn,
                    ds,
                    last_final,
                    "missing",
                    clock.now(),
                    f"NSE's file shows {shown}; this session's deals were never captured",
                )
            report.missing += await _record_skipped_deal_sessions(
                conn, settings, ds, last_final, clock.now()
            )


async def _stored_changes(conn: Any) -> list[SymbolChange]:
    return [
        SymbolChange(r["old_symbol"], r["new_symbol"], r["effective_date"], r["company"])
        for r in await conn.fetch("SELECT * FROM app.symbol_changes")
    ]


async def run_reference_load(
    pool: Any,
    client: NseArchiveClient,
    settings: Settings,
    clock: Clock,
    last_final: date,
    datasets: Sequence[str] = DATASETS,
    progress: Callable[[ReferenceReport], None] | None = None,
    retry_missing: bool = False,
) -> ReferenceReport:
    report = ReferenceReport(started_at=clock.now())
    unknown = [d for d in datasets if d not in DATASETS]
    if unknown:
        raise ValueError(f"unknown reference dataset(s): {unknown}")
    dated = [ds for ds in datasets if ds in DATED_DATASETS]  # deals are forward-only
    async with pool.acquire() as conn:
        todo = {
            ds: await dates_to_load(conn, settings, ds, last_final, retry_missing) for ds in dated
        }
        universe = await nse_universe(conn)
        changes = await _stored_changes(conn)
    report.todo = {ds: len(v) for ds, v in todo.items()}
    needs_names = any(todo.get(ds) for ds in (MTO_DATASET, *PR_DATASETS))
    if needs_names:
        if not universe:
            report.stopped_reason = "universe is empty — refresh the universe first"
            return report
        if not changes:
            report.stopped_reason = (
                "no NSE symbol-change list stored (run the NSE archive load first) — renamed "
                "symbols would be mis-mapped; cannot load symbol-level reference data"
            )
            return report
    if any(ds in DEAL_DATASETS for ds in datasets):
        try:
            await load_deals(pool, client, settings, clock, last_final, report, datasets)
        except NseArchiveRefused as exc:
            report.stopped_reason = str(exc)
            return report
        except NseArchiveError as exc:  # unreachable: the dated datasets still load
            report.errors += 1
            log.warning("deals: %s", exc)
    tolerance = settings.participant_total_tolerance_contracts
    # Both PR datasets use the same zip. Fetch once and ledger each dataset independently.
    plan: dict[tuple[date, str], list[str]] = {}
    for ds, dates in todo.items():
        for d in dates:
            if ds == MTO_DATASET:
                url = mto_url(settings.nse_mto_base_url, d)
            elif ds in PR_DATASETS:
                url = archive_url(settings.nse_archive_base_url, d)
            else:
                url = participant_url(settings.nse_participant_base_url, ds, d)
            plan.setdefault((d, url), []).append(ds)
    for (d, url), file_datasets in sorted(plan.items(), reverse=True):
        error: str | None = None
        try:
            blob = await client.fetch_file(url)
        except NseArchiveRefused as exc:
            report.stopped_reason = str(exc)
            log.error("reference load: %s — stopping", exc)
            break
        except NseArchiveError as exc:
            blob = None
            error = str(exc)
            log.warning("reference %s: %s", url, exc)
        for ds in file_datasets:
            async with pool.acquire() as conn, conn.transaction():
                if error is not None:
                    report.errors += 1
                    await _ledger(conn, ds, d, "error", clock.now(), error)
                elif blob is None:
                    report.missing += 1
                    await _ledger(conn, ds, d, "missing", clock.now(), "404 from the archive")
                else:
                    try:
                        if ds == MTO_DATASET:
                            n, rej = await store_mto(
                                conn, d, blob, names_on(d, universe, changes), clock.now()
                            )
                            report.delivery_rows += n
                            report.rejected += rej
                        elif ds == PR_TEXT_DATASET:
                            n_an, n_bm, rej = await store_pr_text(
                                conn, d, blob, names_on(d, universe, changes), settings, clock.now()
                            )
                            report.announcements += n_an
                            report.board_meetings += n_bm
                            report.rejected += rej
                        elif ds == MARKET_CAP_DATASET:
                            n, rej = await store_market_cap(
                                conn, d, blob, names_on(d, universe, changes), settings, clock.now()
                            )
                            report.market_cap_rows += n
                            report.rejected += rej
                        else:
                            n, gap = await store_participants(
                                conn, ds, d, blob, tolerance, clock.now()
                            )
                            report.participant_rows += n
                            report.rounding_days += 1 if gap else 0
                    except NseArchiveMislabeled as exc:  # no correct file for the day: a gap
                        report.missing += 1
                        await _ledger(conn, ds, d, "missing", clock.now(), str(exc))
                        log.warning("reference %s %s: %s — recorded as a gap", ds, d, exc)
                    except NseArchiveError as exc:  # unreadable file: recorded, not fatal
                        report.errors += 1
                        await _ledger(conn, ds, d, "error", clock.now(), str(exc))
                    else:
                        report.ok += 1
            report.done += 1
            if progress:
                progress(report)
    log.info("reference load: %s", report.totals())
    return report
