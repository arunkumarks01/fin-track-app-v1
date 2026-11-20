"""In-process ingestion jobs (single process — law 5). One job at a time, visible status."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import asyncpg
import httpx

from app.broker.kite.client import KiteAuthError, KiteClient, KiteError, RateLimiter
from app.core.clock import Clock
from app.core.config import Settings
from app.data.adjustment import AnalysisReport, run_adjustment_analysis
from app.data.extract_daily import RunReport, last_final_daily_session, run_daily_backfill
from app.data.extract_intraday import IntradayReport, run_intraday_backfill
from app.data.instruments import (
    InstrumentsError,
    build_universe,
    parse_instruments_csv,
    replace_instruments,
    sync_universe,
)
from app.data.nse_archive import NseArchiveClient, NseArchiveError
from app.data.nse_load import NseLoadReport, run_nse_archive_load
from app.data.repair import repair_provisional_daily

log = logging.getLogger(__name__)

DAILY_INGEST_JOB_ID = "daily_ingest"
INTRADAY_INGEST_JOB_ID = "intraday_ingest"

# connectivity losses worth an automatic resume (the run is resumable from its cursors)
DB_LOST: tuple[type[Exception], ...] = (
    OSError,
    TimeoutError,
    asyncpg.InterfaceError,
    asyncpg.PostgresConnectionError,
)


@dataclass
class JobState:
    name: str
    status: str = "idle"  # idle | running | ok | failed
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "started_at": self.started_at and self.started_at.isoformat(),
            "finished_at": self.finished_at and self.finished_at.isoformat(),
            "result": self.result,
            "error": self.error,
        }


class IngestRunner:
    def __init__(
        self,
        pool: Any,
        client: KiteClient | None,
        settings: Settings,
        clock: Clock,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._sleep = sleep
        self._http = http
        self._pool = pool
        self._client = client
        self._s = settings
        self._clock = clock
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self.last = JobState("none")
        # newest first; in memory only, so it starts empty after a restart (the Admin page says so)
        self.history: deque[JobState] = deque(maxlen=settings.job_history_size)

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def _require_client(self) -> KiteClient:
        if self._client is None:
            raise KiteAuthError("Kite is not configured (KITE_API_KEY / KITE_API_SECRET)")
        return self._client

    async def refresh_universe(self) -> dict[str, int]:
        client = self._require_client()
        text = await client.instruments_csv()
        instruments = parse_instruments_csv(text)
        members = build_universe(instruments, self._s.universe_index_symbols)
        now = self._clock.now()
        async with self._pool.acquire() as conn:
            stored = await replace_instruments(conn, instruments, now)
            counts = await sync_universe(conn, members, now)
        return {"instruments": stored, **counts}

    async def backfill_daily(
        self, symbols: list[str] | None = None, reset: bool = False
    ) -> RunReport:
        return await run_daily_backfill(
            self._pool, self._require_client(), self._s, self._clock, symbols, reset=reset
        )

    async def _run(self, name: str, work: Callable[[], Awaitable[dict[str, Any]]]) -> JobState:
        state = JobState(name, "running", started_at=self._clock.now())
        self.last = state
        self.history.appendleft(state)
        async with self._lock:
            try:
                state.result = await work()
                state.status = "failed" if state.result.get("stopped_reason") else "ok"
                state.error = state.result.get("stopped_reason")
            except (KiteError, InstrumentsError, NseArchiveError, ValueError) as exc:
                state.status, state.error = "failed", str(exc)
                log.error("job %s failed: %s", name, exc)
            except Exception as exc:  # never let a job kill the process silently
                state.status, state.error = "failed", f"{type(exc).__name__}: {exc}"
                log.exception("job %s crashed", name)
            finally:
                state.finished_at = self._clock.now()
        return state

    async def daily_ingest(
        self, symbols: list[str] | None = None, refresh: bool = True, reset: bool = False
    ) -> JobState:
        async def work() -> dict[str, Any]:
            out: dict[str, Any] = {}
            if refresh:
                out["universe"] = await self.refresh_universe()
            report = await self.backfill_daily(symbols, reset=reset)
            if symbols:
                out["symbols"] = symbols
                out["reset"] = reset
            out["backfill"] = report.totals()
            out["stopped_reason"] = report.stopped_reason
            if report.stopped_reason is None and self._s.nse_archive_after_daily:
                # official prices + measured adjustments for the sessions just stored
                after = await self._nse_and_analysis(self.last)
                out.update({k: v for k, v in after.items() if k != "stopped_reason"})
                out["stopped_reason"] = after["stopped_reason"]
            out["errors"] = [
                {"symbol": r.symbol, "error": r.error} for r in report.results if r.error
            ][:50]
            return out

        return await self._run("daily_ingest", work)

    async def intraday_ingest(self, symbols: list[str] | None = None) -> JobState:
        """Newest-first 1m backfill + catch-up. Progress is published live in the job state."""

        async def work() -> dict[str, Any]:
            state = self.last

            def progress(report: IntradayReport) -> None:
                state.result = {"progress": report.totals()}

            client = self._require_client()
            attempt = 0
            while True:
                attempt += 1
                try:
                    report = await run_intraday_backfill(
                        self._pool, client, self._s, self._clock, symbols, progress
                    )
                    break
                except DB_LOST as exc:
                    if attempt > self._s.intraday_db_retries:
                        raise
                    log.warning(
                        "intraday: database lost (%s); resume attempt %d/%d in %.0fs",
                        type(exc).__name__,
                        attempt,
                        self._s.intraday_db_retries,
                        self._s.intraday_db_retry_wait_seconds,
                    )
                    state.result = {**state.result, "resumes": attempt}
                    await self._sleep(self._s.intraday_db_retry_wait_seconds)
            return {
                "resumes": attempt - 1,
                "intraday": report.totals(),
                "stopped_reason": report.stopped_reason,
                "errors": [{"symbol": s, "error": e} for s, e in report.errors.items()][:50],
            }

        return await self._run("intraday_ingest", work)

    # --- Phase 1c: NSE archive, measured adjustments, audited repair ---------------------

    def _nse_client(self) -> NseArchiveClient:
        if self._http is None:
            raise NseArchiveError("no HTTP client configured for the NSE archive")
        limiter = RateLimiter(self._s.nse_archive_rps, self._clock, self._sleep)
        return NseArchiveClient(self._http, self._s, limiter, self._sleep)

    async def _nse_and_analysis(
        self, state: JobState, retry_missing: bool = False
    ) -> dict[str, Any]:
        def nse_progress(r: NseLoadReport) -> None:
            state.result = {**state.result, "progress": r.totals()}

        last_final = last_final_daily_session(
            self._clock.now(), self._s.daily_bar_final_next_day_ist
        )
        nse = await run_nse_archive_load(
            self._pool,
            self._nse_client(),
            self._s,
            self._clock,
            last_final,
            nse_progress,
            retry_missing,
        )
        out: dict[str, Any] = {"nse": nse.totals(), "stopped_reason": nse.stopped_reason}
        if nse.stopped_reason is None:

            def adj_progress(r: AnalysisReport) -> None:
                state.result = {**state.result, "progress": r.totals()}

            adj = await run_adjustment_analysis(
                self._pool, self._s, self._clock, progress=adj_progress
            )
            out["adjustments"] = adj.totals()
        return out

    async def nse_archive_ingest(self, retry_missing: bool = False) -> JobState:
        """Incremental NSE archive load, then re-measure every symbol's adjustment."""

        async def work() -> dict[str, Any]:
            return await self._nse_and_analysis(self.last, retry_missing)

        return await self._run("nse_archive_ingest", work)

    async def adjustment_analysis(self, symbols: list[str] | None = None) -> JobState:
        async def work() -> dict[str, Any]:
            state = self.last

            def progress(r: AnalysisReport) -> None:
                state.result = {"progress": r.totals()}

            report = await run_adjustment_analysis(
                self._pool, self._s, self._clock, symbols, progress
            )
            return {"adjustments": report.totals(), "stopped_reason": None}

        return await self._run("adjustment_analysis", work)

    async def repair_provisional(self) -> JobState:
        async def work() -> dict[str, Any]:
            report = await repair_provisional_daily(
                self._pool, self._require_client(), self._s, self._clock
            )
            return {
                "repair": report.totals(),
                "changes": report.changes[:300],
                "errors": [{"symbol": k, "error": v} for k, v in report.failed.items()][:50],
                "stopped_reason": None,
            }

        return await self._run("repair_provisional", work)

    def start(self, coro_factory: Callable[[], Awaitable[JobState]]) -> bool:
        """Start a background job unless one is running. Returns False when busy."""
        if self.busy or (self._task is not None and not self._task.done()):
            return False

        async def runner() -> None:
            await coro_factory()

        self._task = asyncio.get_running_loop().create_task(runner())
        return True

    async def wait(self) -> None:
        if self._task is not None:
            await self._task

    def cancel(self) -> None:
        """On shutdown: the in-flight window's transaction rolls back; the cursor stays true."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
