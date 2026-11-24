"""FinTrack v2 — single-process FastAPI app.

Boot order (fail closed — any error aborts startup):
config → masked logging → DB pool → migrations → chunk sizing → Kite session restore →
scheduler → serve.
Run: `uv run python -m app` (or `uvicorn app.main:create_app --factory`).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import httpx
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.admin import router as admin_router
from app.api.analytics import router as analytics_router
from app.api.backtest import router as backtest_router
from app.api.data import router as data_router
from app.api.health import router as health_router
from app.api.kite import router as kite_router
from app.api.paper import router as paper_router
from app.api.scoring import router as scoring_router
from app.broker.kite.client import KiteClient
from app.broker.kite.session import SessionStore
from app.core.auth import BearerAuthMiddleware
from app.core.cache import MemoryCache
from app.core.clock import Clock, SystemClock
from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.core.scheduler import build_scheduler, register_job
from app.core.tradingdate import MARKET_TZ
from app.data.jobs import DAILY_INGEST_JOB_ID, INTRADAY_INGEST_JOB_ID, IngestRunner
from app.data.option_chain import OptionChain
from app.data.ticks import TickCapture
from app.db.migrate import migrate
from app.db.policies import apply_storage_policies
from app.db.pool import create_pool
from app.db.retention import CLEANUP_JOB_ID, ticks_cleanup_job
from app.scoring.news_research import NewsResearch
from app.scoring.scans import scan_universe

__version__ = "0.0.1"

log = logging.getLogger(__name__)

# A lost database (e.g. the dev SSH tunnel dropping) is an honest 503 on every endpoint —
# never a raw 500 and never a stale or guessed answer (laws 2 + 9).
DB_UNAVAILABLE: tuple[type[Exception], ...] = (
    OSError,
    TimeoutError,
    asyncpg.InterfaceError,
    asyncpg.PostgresConnectionError,
)


async def _db_unavailable(request: Request, exc: Exception) -> JSONResponse:
    log.warning(
        "database unavailable on %s %s: %s", request.method, request.url.path, type(exc).__name__
    )
    return JSONResponse(
        {"detail": "database unavailable", "error": type(exc).__name__}, status_code=503
    )


def create_app(
    settings: Settings | None = None,
    clock: Clock | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """`http_transport` lets tests swap the network for `httpx.MockTransport`."""
    cfg = settings if settings is not None else get_settings()
    clk: Clock = clock if clock is not None else SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging(cfg.log_level, cfg.secret_values())
        pool = await create_pool(cfg)
        http = httpx.AsyncClient(transport=http_transport, timeout=cfg.kite_timeout_seconds)
        scheduler = None
        ingest: IngestRunner | None = None
        try:
            async with pool.acquire() as conn:
                await migrate(conn)
                await apply_storage_policies(conn, cfg)
                await conn.execute(
                    """UPDATE app.backtest_runs SET status='failed',
                              detail='Process restarted before result commit; rerun required'
                       WHERE status IN ('queued','running')"""
                )
            sessions = SessionStore(pool, clk)
            restored = await sessions.load()
            log.info("kite session %s", "restored" if restored else "not available")
            kite = KiteClient(http, cfg, sessions, clk) if cfg.kite_configured else None
            ingest = IngestRunner(pool, kite, cfg, clk, http=http)
            scheduler = build_scheduler(cfg)
            capture = TickCapture(pool, sessions, cfg, clk)
            chain = OptionChain(pool, kite, cfg, clk)

            async def capture_ticks() -> dict[str, Any]:
                result = await capture.run()
                return {
                    **result.to_dict(),
                    "stopped_reason": result.detail if result.status != "ok" else None,
                }

            async def snapshot_chain() -> dict[str, Any]:
                result = await chain.snapshot()
                return {**result.totals(), "stopped_reason": result.stopped_reason}

            async def scan_swing() -> dict[str, Any]:
                return await scan_universe(pool, cfg, clk.now(), "swing")

            async def scan_intraday() -> dict[str, Any]:
                return await scan_universe(pool, cfg, clk.now(), "intraday")

            services = {
                "ticks_capture": ingest.service("ticks_capture", capture_ticks),
                "option_chain": ingest.service("option_chain", snapshot_chain),
                "scoring_swing": ingest.service("scoring_swing", scan_swing),
                "scoring_intraday": ingest.service("scoring_intraday", scan_intraday),
            }
            if kite is not None:
                for name, cron in (
                    ("ticks_capture", cfg.ticks_capture_cron),
                    ("option_chain", cfg.chain_cron),
                ):
                    register_job(
                        scheduler,
                        services[name],
                        CronTrigger.from_crontab(cron, timezone=MARKET_TZ),
                        job_id=name,
                    )
            if cfg.scoring_scan_enabled:
                for job_id, service, cron in (
                    ("scoring_swing_morning", "scoring_swing", cfg.scoring_swing_morning_cron),
                    ("scoring_swing_close", "scoring_swing", cfg.scoring_swing_close_cron),
                    ("scoring_intraday", "scoring_intraday", cfg.scoring_intraday_cron),
                ):
                    register_job(
                        scheduler,
                        services[service],
                        CronTrigger.from_crontab(cron, timezone=MARKET_TZ),
                        job_id=job_id,
                    )
            register_job(
                scheduler,
                ticks_cleanup_job(pool, clk, cfg),
                CronTrigger.from_crontab(cfg.ticks_retention_cron, timezone=MARKET_TZ),
                job_id=CLEANUP_JOB_ID,
            )
            register_job(
                scheduler,
                ingest.daily_ingest,
                CronTrigger.from_crontab(cfg.daily_ingest_cron, timezone=MARKET_TZ),
                job_id=DAILY_INGEST_JOB_ID,
            )
            register_job(
                scheduler,
                ingest.intraday_ingest,
                CronTrigger.from_crontab(cfg.intraday_ingest_cron, timezone=MARKET_TZ),
                job_id=INTRADAY_INGEST_JOB_ID,
            )
            register_job(
                scheduler,
                ingest.news_fetch,
                CronTrigger.from_crontab(cfg.news_fetch_cron, timezone=MARKET_TZ),
                job_id="news_fetch",
            )
            scheduler.start()
            app.state.pool = pool
            app.state.scheduler = scheduler
            app.state.cache = MemoryCache(clk, cfg.cache_max_entries)
            app.state.scoring_news = NewsResearch(app.state.cache, cfg.scoring)
            app.state.http = http
            app.state.kite_sessions = sessions
            app.state.ingest = ingest
            app.state.capture = capture
            app.state.chain = chain
            app.state.job_services = services
            app.state.started_at = clk.now()
            log.info("FinTrack v2 %s started (env=%s, PAPER_MODE=true)", __version__, cfg.app_env)
            yield
        finally:
            if ingest is not None:
                await ingest.cancel()
            if scheduler is not None and scheduler.running:
                scheduler.shutdown(wait=False)
            await http.aclose()
            await pool.close()
            log.info("FinTrack v2 stopped")

    app = FastAPI(title="FinTrack v2", version=__version__, lifespan=lifespan)
    app.state.settings = cfg
    app.state.clock = clk
    app.add_middleware(BearerAuthMiddleware, token=cfg.api_auth_token)
    for exc_type in DB_UNAVAILABLE:
        app.add_exception_handler(exc_type, _db_unavailable)
    app.include_router(health_router)
    app.include_router(kite_router)
    app.include_router(data_router)
    app.include_router(admin_router)
    app.include_router(analytics_router)
    app.include_router(scoring_router)
    app.include_router(paper_router)
    app.include_router(backtest_router)
    return app
