from __future__ import annotations

import pytest
from apscheduler.triggers.interval import IntervalTrigger

from app.core.scheduler import build_scheduler, register_job
from app.core.tradingdate import MARKET_TZ
from tests.conftest import SettingsFactory


async def _noop() -> None:
    return None


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
