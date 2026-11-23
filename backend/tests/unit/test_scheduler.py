from __future__ import annotations

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.scheduler import build_scheduler, register_job
from app.core.tradingdate import MARKET_TZ
from tests.conftest import SettingsFactory


async def _noop() -> None:
    return None


def test_default_market_schedules_include_monday_and_exclude_weekend(
    make_settings: SettingsFactory,
) -> None:
    from datetime import datetime

    cfg = make_settings()
    monday = datetime(2026, 11, 23, 0, 0, tzinfo=MARKET_TZ)
    saturday = datetime(2026, 11, 28, 0, 0, tzinfo=MARKET_TZ)
    for expression in (cfg.ticks_capture_cron, cfg.chain_cron, cfg.intraday_ingest_cron):
        trigger = CronTrigger.from_crontab(expression, timezone=MARKET_TZ)
        assert trigger.get_next_fire_time(None, monday).weekday() == 0
        assert trigger.get_next_fire_time(None, saturday).weekday() == 0
    daily = CronTrigger.from_crontab(cfg.daily_ingest_cron, timezone=MARKET_TZ)
    assert daily.get_next_fire_time(None, saturday).weekday() == 5


async def test_jobs_get_single_instance_and_coalesce(make_settings: SettingsFactory) -> None:
    scheduler = build_scheduler(make_settings(scheduler_misfire_grace_seconds=42))
    scheduler.start(paused=True)
    try:
        job = register_job(scheduler, _noop, IntervalTrigger(minutes=5), job_id="probe")
        assert job.max_instances == 1
        assert job.coalesce is True
        assert job.misfire_grace_time == 42
        assert str(scheduler.timezone) == str(MARKET_TZ)
    finally:
        scheduler.shutdown(wait=False)


async def test_starts_empty(make_settings: SettingsFactory) -> None:
    scheduler = build_scheduler(make_settings())
    scheduler.start(paused=True)
    try:
        assert scheduler.get_jobs() == []
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.parametrize("override", [{"max_instances": 2}, {"coalesce": False}])
def test_fixed_defaults_cannot_be_overridden(
    make_settings: SettingsFactory, override: dict[str, object]
) -> None:
    scheduler = build_scheduler(make_settings())
    with pytest.raises(ValueError, match="fixed"):
        register_job(scheduler, _noop, IntervalTrigger(minutes=5), job_id="x", **override)


# --- intraday job auto-resume after a lost database (runner unit, no DB) ---
async def test_intraday_job_resumes_after_db_loss_but_not_after_budget(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from app.data import jobs
    from app.data.extract_intraday import IntradayReport
    from tests.fixtures.clock import FrozenClock

    outcomes: list[object] = [
        ConnectionResetError("reset by peer"),
        OSError("Connect call failed"),
        IntradayReport(started_at=datetime(2026, 1, 1, tzinfo=UTC), inserted=42),
    ]

    async def fake_run(*_a: object, **_k: object) -> IntradayReport:
        nxt = outcomes.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        assert isinstance(nxt, IntradayReport)
        return nxt

    waits: list[float] = []

    async def sleep(s: float) -> None:
        waits.append(s)

    monkeypatch.setattr(jobs, "run_intraday_backfill", fake_run)
    s = make_settings(
        kite_api_key="k" * 10, kite_api_secret="s" * 10, intraday_db_retry_wait_seconds=7
    )
    runner = jobs.IngestRunner(
        None,
        object(),  # type: ignore[arg-type]  # client is never called: the run is faked
        s,
        FrozenClock(datetime(2026, 1, 1, tzinfo=UTC)),
        sleep,
    )
    state = await runner.intraday_ingest()
    assert state.status == "ok"
    assert state.result["resumes"] == 2
    assert state.result["intraday"]["inserted"] == 42
    assert waits == [7, 7]

    # a budget stop is a deliberate fail-closed stop: reported, never auto-retried
    budget = IntradayReport(
        started_at=datetime(2026, 1, 1, tzinfo=UTC), stopped_reason="disk budget reached"
    )
    outcomes[:] = [budget]
    waits.clear()
    state = await runner.intraday_ingest()
    assert state.status == "failed"
    assert state.error == "disk budget reached"
    assert waits == []


async def test_intraday_job_gives_up_after_retry_limit(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from app.data import jobs
    from tests.fixtures.clock import FrozenClock

    async def always_lost(*_a: object, **_k: object) -> None:
        raise OSError("Connect call failed")

    async def sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(jobs, "run_intraday_backfill", always_lost)
    s = make_settings(kite_api_key="k" * 10, kite_api_secret="s" * 10, intraday_db_retries=2)
    runner = jobs.IngestRunner(
        None,
        object(),  # type: ignore[arg-type]  # client is never called: the run is faked
        s,
        FrozenClock(datetime(2026, 1, 1, tzinfo=UTC)),
        sleep,
    )
    state = await runner.intraday_ingest()
    assert state.status == "failed"
    assert state.error is not None
    assert "Connect call failed" in state.error


async def test_job_history_is_bounded_newest_first(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from app.data import jobs
    from app.data.extract_intraday import IntradayReport
    from tests.fixtures.clock import FrozenClock

    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    reports = iter(
        IntradayReport(started_at=clock.now(), inserted=n, stopped_reason=stop)
        for n, stop in [(1, None), (2, "disk budget reached"), (3, None)]
    )

    async def fake_run(*_a: object, **_k: object) -> IntradayReport:
        return next(reports)

    monkeypatch.setattr(jobs, "run_intraday_backfill", fake_run)
    s = make_settings(kite_api_key="k" * 10, kite_api_secret="s" * 10, job_history_size=2)
    runner = jobs.IngestRunner(None, object(), s, clock)  # type: ignore[arg-type]
    for _ in range(3):
        await runner.intraday_ingest()
    assert [(r.status, r.result["intraday"]["inserted"]) for r in runner.history] == [
        ("ok", 3),
        ("failed", 2),
    ]
    assert runner.history[0] is runner.last
