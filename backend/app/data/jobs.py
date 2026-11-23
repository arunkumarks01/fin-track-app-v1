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
from app.data.fo_load import FoReport, run_fo_load
from app.data.fred import fetch_fred
from app.data.instruments import (
    InstrumentsError,
    build_universe,
    parse_instruments_csv,
    replace_instruments,
    sync_universe,
)
from app.data.news import fetch_news
from app.data.nse_archive import NseArchiveClient, NseArchiveError
from app.data.nse_load import NseLoadReport, run_nse_archive_load
from app.data.reference_load import ReferenceReport, run_reference_load
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
    status: str = "idle"  # idle | running | ok | failed | stopped
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
        # Quick jobs run independently of long downloads and of each other.
        self._news_lock = asyncio.Lock()
        self._fred_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._active: dict[str, tuple[JobState, asyncio.Task[Any]]] = {}
        self._background: dict[str, asyncio.Task[JobState]] = {}
        self._service_locks: dict[str, asyncio.Lock] = {}
        self.last = JobState("none")
        # newest first; in memory only, so it starts empty after a restart (the Admin page says so)
        self.history: deque[JobState] = deque(maxlen=settings.job_history_size)

    @property
    def busy(self) -> bool:
        return self._lock.locked() or (self._task is not None and not self._task.done())

    @property
    def active(self) -> bool:
        return self.busy or bool(self._active or self._background)

    async def _execute(
        self, state: JobState, work: Callable[[], Awaitable[dict[str, Any]]]
    ) -> JobState:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Job execution requires an asyncio task")
        name = state.name
        self._active[name] = state, task
        try:
            state.result = await work()
            state.error = state.result.get("stopped_reason")
            state.status = "failed" if state.error else "ok"
        except asyncio.CancelledError:
            state.status = "stopped"
            raise
        except (KiteError, InstrumentsError, NseArchiveError, ValueError) as exc:
            state.status, state.error = "failed", str(exc)
            log.error("job %s failed: %s", name, exc)
        except Exception as exc:
            state.status, state.error = "failed", f"{type(exc).__name__}: {exc}"
            log.exception("job %s crashed", name)
        finally:
            self._active.pop(name)
            state.finished_at = self._clock.now()
        return state

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
            self._pool,
            self._require_client(),
            self._s,
            self._clock,
            symbols,
            reset=reset,
            include_reference_indices=True,
        )

    async def _run(self, name: str, work: Callable[[], Awaitable[dict[str, Any]]]) -> JobState:
        state = JobState(name, "running", started_at=self._clock.now())
        self.history.appendleft(state)
        if self._lock.locked() or (self.busy and asyncio.current_task() is not self._task):
            state.status, state.error = "failed", "Another ingestion job is already running"
            state.finished_at = self._clock.now()
            return state
        self.last = state
        async with self._lock:
            return await self._execute(state, work)

    async def _run_light(
        self, name: str, lock: asyncio.Lock, work: Callable[[], Awaitable[dict[str, Any]]]
    ) -> JobState:
        state = JobState(name, "running", started_at=self._clock.now())
        self.history.appendleft(state)
        if lock.locked():
            state.status, state.error = "failed", f"{name}: already running"
            state.finished_at = self._clock.now()
            return state
        async with lock:
            return await self._execute(state, work)

    def service(
        self, name: str, work: Callable[[], Awaitable[dict[str, Any]]]
    ) -> Callable[[], Awaitable[JobState]]:
        lock = self._service_locks.setdefault(name, asyncio.Lock())

        async def run() -> JobState:
            return await self._run_light(name, lock, work)

        return run

    def start_service(self, name: str, work: Callable[[], Awaitable[JobState]]) -> bool:
        if name in self._active or name in self._background:
            return False

        async def run() -> JobState:
            return await work()

        task = asyncio.create_task(run())
        self._background[name] = task
        task.add_done_callback(lambda _: self._background.pop(name, None))
        return True

    def stop(self, name: str, started_at: datetime) -> bool:
        active = self._active.get(name)
        if active is None:
            return False
        state, task = active
        # Daily ingestion can invoke FRED in the same task. Stop the owning run only.
        if state.started_at != started_at or task.cancelling():
            return False
        if state is not self.last and any(
            other is task for key, (_, other) in self._active.items() if key != name
        ):
            return False
        return task.cancel()

    async def _resuming(self, name: str, state: JobState, run: Callable[[], Awaitable[Any]]) -> Any:
        """Run a resumable load; if the database connection is lost (the SSH tunnel drops every
        ~20-30 min), wait and run it again. Every loader skips what its ledger already holds, so
        a re-run continues where it stopped. Gives up after INTRADAY_DB_RETRIES in a row."""
        attempt = 0
        while True:
            try:
                result = await run()
            except DB_LOST as exc:
                attempt += 1
                if attempt > self._s.intraday_db_retries:
                    raise
                log.warning(
                    "%s: database lost (%s); resume %d/%d in %.0fs",
                    name,
                    type(exc).__name__,
                    attempt,
                    self._s.intraday_db_retries,
                    self._s.intraday_db_retry_wait_seconds,
                )
                state.result = {
                    **state.result,
                    "resumes": attempt,
                    "retry_error": f"{type(exc).__name__}: database connection interrupted",
                }
                await self._sleep(self._s.intraday_db_retry_wait_seconds)
            else:
                return result

    async def news_fetch(self) -> JobState:
        async def work() -> dict[str, Any]:
            if self._http is None:
                return {"stopped_reason": "no HTTP client configured"}
            report = await fetch_news(self._pool, self._http, self._s, self._clock)
            return report.totals()

        return await self._run_light("news_fetch", self._news_lock, work)

    async def fred_fetch(self) -> JobState:
        async def work() -> dict[str, Any]:
            if self._http is None:
                return {"stopped_reason": "no HTTP client configured"}
            report = await fetch_fred(self._pool, self._http, self._s, self._clock)
            return {**report.totals(), "stopped_reason": report.stopped_reason}

        return await self._run_light("fred_fetch", self._fred_lock, work)

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
                if out["stopped_reason"] is None and self._s.reference_after_daily:
                    # Phase 1e: NSE's dated files and the latest deal files
                    ref = await self._resuming(
                        "reference", self.last, lambda: self._reference(self.last)
                    )
                    out["resumes"] = self.last.result.get("resumes", 0)
                    out["reference"] = ref.totals()
                    out["stopped_reason"] = ref.stopped_reason
            if report.stopped_reason is None and self._s.fred_api_key is not None:
                out["fred"] = (await self.fred_fetch()).to_dict()
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
                        self._pool,
                        client,
                        self._s,
                        self._clock,
                        symbols,
                        progress,
                        include_reference_indices=True,
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
            out: dict[str, Any] = await self._resuming(
                "nse archive",
                self.last,
                lambda: self._nse_and_analysis(self.last, retry_missing),
            )
            return out

        return await self._run("nse_archive_ingest", work)

    # --- Phase 1e: reference data from the NSE archive --------------------------------------

    async def _reference(
        self,
        state: JobState,
        datasets: list[str] | None = None,
        retry_missing: bool = False,
    ) -> ReferenceReport:
        def progress(r: ReferenceReport) -> None:
            state.result = {**state.result, "progress": r.totals()}

        last_final = last_final_daily_session(
            self._clock.now(), self._s.daily_bar_final_next_day_ist
        )
        kwargs: dict[str, Any] = {"progress": progress, "retry_missing": retry_missing}
        if datasets:
            kwargs["datasets"] = datasets
        return await run_reference_load(
            self._pool, self._nse_client(), self._s, self._clock, last_final, **kwargs
        )

    async def fo_history_ingest(self, retry_missing: bool = False) -> JobState:
        """Slice 1f: daily futures and options history from NSE's F&O bhavcopy archive."""

        async def work() -> dict[str, Any]:
            def progress(r: FoReport) -> None:
                self.last.result = {**self.last.result, "progress": r.totals()}

            last_final = last_final_daily_session(
                self._clock.now(), self._s.daily_bar_final_next_day_ist
            )
            report = await self._resuming(
                "fo history",
                self.last,
                lambda: run_fo_load(
                    self._pool,
                    self._nse_client(),
                    self._s,
                    self._clock,
                    last_final,
                    progress,
                    retry_missing,
                ),
            )
            return {
                "resumes": self.last.result.get("resumes", 0),
                "fo": report.totals(),
                "stopped_reason": report.stopped_reason,
            }

        return await self._run("fo_history_ingest", work)

    async def reference_ingest(
        self, datasets: list[str] | None = None, retry_missing: bool = False
    ) -> JobState:
        """Load the requested NSE reference datasets, resuming from their ledgers."""

        async def work() -> dict[str, Any]:
            report = await self._resuming(
                "reference", self.last, lambda: self._reference(self.last, datasets, retry_missing)
            )
            return {
                "resumes": self.last.result.get("resumes", 0),
                "reference": report.totals(),
                "stopped_reason": report.stopped_reason
                or (
                    f"{report.errors} reference files could not be loaded"
                    if report.errors
                    else None
                ),
            }

        return await self._run("reference_ingest", work)

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
        if self.busy:
            return False

        async def runner() -> None:
            try:
                await coro_factory()
            except asyncio.CancelledError:
                return

        self._task = asyncio.get_running_loop().create_task(runner())
        return True

    async def wait(self) -> None:
        if self._task is not None:
            await self._task

    async def cancel(self) -> None:
        """On shutdown: the in-flight window's transaction rolls back; the cursor stays true."""
        tasks = {task for _, task in self._active.values()} | set(self._background.values())
        if self._task is not None:
            tasks.add(self._task)
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
