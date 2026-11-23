"""A resumable load survives a lost database connection (the SSH tunnel drops every ~20-30 min)."""

from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
import pytest

from app.data.extract_daily import RunReport
from app.data.jobs import IngestRunner, JobState
from app.data.reference_load import ReferenceReport
from tests.conftest import build_settings
from tests.fixtures.clock import FrozenClock


def runner(retries: int) -> tuple[IngestRunner, list[float]]:
    waits: list[float] = []

    async def sleep(s: float) -> None:
        waits.append(s)

    s = build_settings(
        intraday_db_retries=retries,
        intraday_db_retry_wait_seconds=30.0,
        nse_archive_after_daily=True,
        reference_after_daily=True,
    )
    return IngestRunner(
        None, None, s, FrozenClock(datetime(2026, 11, 21, 4, 0, tzinfo=UTC)), sleep
    ), waits


async def test_a_lost_connection_waits_and_runs_again_until_it_works() -> None:
    r, waits = runner(retries=5)
    state = JobState("t", "running")
    calls = 0

    async def run() -> str:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise asyncpg.ConnectionDoesNotExistError("connection was closed")
        return "done"

    assert await r._resuming("t", state, run) == "done"
    assert (calls, waits, state.result["resumes"]) == (3, [30.0, 30.0], 2)


async def test_it_gives_up_after_the_limit_and_other_errors_are_not_retried() -> None:
    r, waits = runner(retries=2)
    state = JobState("t", "running")

    async def always_lost() -> None:
        raise asyncpg.InterfaceError("underlying connection is closed")

    with pytest.raises(asyncpg.InterfaceError):
        await r._resuming("t", state, always_lost)
    assert len(waits) == 2

    async def bug() -> None:
        raise ValueError("a real bug")

    with pytest.raises(ValueError, match="real bug"):
        await r._resuming("t", state, bug)
    assert len(waits) == 2  # no further waiting for a non-connection error


async def test_daily_ingest_resumes_its_reference_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    r, waits = runner(retries=2)
    now = datetime(2026, 11, 21, 4, 0, tzinfo=UTC)

    async def backfill(symbols: list[str] | None, *, reset: bool) -> RunReport:
        return RunReport(now)

    async def archive(state: JobState) -> dict[str, None]:
        return {"stopped_reason": None}

    calls = 0

    async def reference(state: JobState) -> ReferenceReport:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncpg.InterfaceError("Transaction.__aexit__(): connection is closed")
        return ReferenceReport(now, ok=1)

    monkeypatch.setattr(r, "backfill_daily", backfill)
    monkeypatch.setattr(r, "_nse_and_analysis", archive)
    monkeypatch.setattr(r, "_reference", reference)
    state = await r.daily_ingest(refresh=False)
    assert state.status == "ok"
    assert state.result["reference"]["ok"] == 1
    assert (calls, waits, state.result["resumes"]) == (2, [30.0], 1)
