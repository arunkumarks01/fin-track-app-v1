"""In-process APScheduler (single process → no distributed lock needed).

`max_instances=1` and `coalesce=True` are fixed by design (no double-runs, missed runs
collapse into one) and cannot be overridden per job. No jobs are registered in Phase 0.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ

FIXED_JOB_DEFAULTS: dict[str, Any] = {"max_instances": 1, "coalesce": True}
_LOCKED_KEYS = frozenset(FIXED_JOB_DEFAULTS)


def build_scheduler(settings: Settings) -> AsyncIOScheduler:
    return AsyncIOScheduler(
        timezone=MARKET_TZ,
        job_defaults={
            **FIXED_JOB_DEFAULTS,
            "misfire_grace_time": settings.scheduler_misfire_grace_seconds,
        },
    )


def register_job(
    scheduler: AsyncIOScheduler,
    func: Callable[..., Any],
    trigger: Any,
    *,
    job_id: str,
    **kwargs: Any,
) -> Any:
    """The only sanctioned way to add a job: refuses to weaken the fixed defaults."""
    overridden = _LOCKED_KEYS & kwargs.keys()
    if overridden:
        raise ValueError(f"job defaults {sorted(overridden)} are fixed and cannot be overridden")
    return scheduler.add_job(func, trigger, id=job_id, replace_existing=True, **kwargs)
