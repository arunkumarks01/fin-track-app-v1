"""FinTrack v2 — single-process FastAPI app.

Boot order (fail closed — any error aborts startup):
config → masked logging → DB pool → migrations → storage policies → scheduler → serve.
Run: `uv run python -m app` (or `uvicorn app.main:create_app --factory`).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.auth import BearerAuthMiddleware
from app.core.cache import MemoryCache
from app.core.clock import Clock, SystemClock
from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.core.scheduler import build_scheduler, register_job
from app.core.tradingdate import MARKET_TZ
from app.db.migrate import migrate
from app.db.policies import apply_storage_policies
from app.db.pool import create_pool
from app.db.retention import CLEANUP_JOB_ID, ticks_cleanup_job

__version__ = "0.0.1"

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    cfg = settings if settings is not None else get_settings()
    clk: Clock = clock if clock is not None else SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging(cfg.log_level, cfg.secret_values())
        pool = await create_pool(cfg)
        scheduler = None
        try:
            async with pool.acquire() as conn:
                await migrate(conn)
                await apply_storage_policies(conn, cfg)
            scheduler = build_scheduler(cfg)
            register_job(
                scheduler,
                ticks_cleanup_job(pool, clk, cfg),
                CronTrigger.from_crontab(cfg.ticks_retention_cron, timezone=MARKET_TZ),
                job_id=CLEANUP_JOB_ID,
            )
            scheduler.start()
            app.state.pool = pool
            app.state.scheduler = scheduler
            app.state.cache = MemoryCache(clk, cfg.cache_max_entries)
            log.info("FinTrack v2 %s started (env=%s, PAPER_MODE=true)", __version__, cfg.app_env)
            yield
        finally:
            if scheduler is not None and scheduler.running:
                scheduler.shutdown(wait=False)
            await pool.close()
            log.info("FinTrack v2 stopped")

    app = FastAPI(title="FinTrack v2", version=__version__, lifespan=lifespan)
    app.state.settings = cfg
    app.add_middleware(BearerAuthMiddleware, token=cfg.api_auth_token)
    app.include_router(health_router)
    return app
