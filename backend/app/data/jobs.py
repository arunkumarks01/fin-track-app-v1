"""In-process ingestion jobs (single process — law 5). One job at a time, visible status."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.broker.kite.client import KiteAuthError, KiteClient, KiteError
from app.core.clock import Clock
from app.core.config import Settings
from app.data.extract_daily import RunReport, run_daily_backfill
from app.data.instruments import (
    InstrumentsError,
    build_universe,
    parse_instruments_csv,
    replace_instruments,
    sync_universe,
)

log = logging.getLogger(__name__)

DAILY_INGEST_JOB_ID = "daily_ingest"


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
        self, pool: Any, client: KiteClient | None, settings: Settings, clock: Clock
    ) -> None:
        self._pool = pool
        self._client = client
        self._s = settings
        self._clock = clock
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self.last = JobState("none")

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
        async with self._lock:
            try:
                state.result = await work()
                state.status = "failed" if state.result.get("stopped_reason") else "ok"
                state.error = state.result.get("stopped_reason")
            except (KiteError, InstrumentsError, ValueError) as exc:
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
            out["errors"] = [
                {"symbol": r.symbol, "error": r.error} for r in report.results if r.error
            ][:50]
            return out

        return await self._run("daily_ingest", work)

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
