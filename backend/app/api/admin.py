"""Admin/Health read-only overview (UX_DESIGN "Admin/Health", the Phase-1 subset).

GET /api/admin/overview → scheduled jobs + recent job runs + quarantine/flag breakdown +
                          symbols whose ingest cursor is in error.

Read-only by design: starting jobs needs the bearer token, which never reaches the browser.
Run history lives in process memory (single process, no extra store), so it starts empty after
a restart and the response says so (`history_since`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.data.jobs import IngestRunner

router = APIRouter(prefix="/api/admin")

_QUARANTINE_SQL = """
SELECT timeframe, reason, count(*) AS n FROM app.ohlcv_quarantine
GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2
"""
_FLAGS_SQL = """
SELECT timeframe, flag, count(*) AS n FROM app.ohlcv_flags GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2
"""
_CURSOR_ERRORS_SQL = """
SELECT '1d' AS grain, symbol, last_error, updated_at FROM app.ingest_cursor WHERE status = 'error'
UNION ALL
SELECT '1m', i.symbol, c.last_error, c.updated_at
FROM app.intraday_cursor c JOIN app.instrument_ids i USING (sid) WHERE c.status = 'error'
ORDER BY 4 DESC LIMIT 50
"""


def _scheduler(request: Request) -> dict[str, Any] | None:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        return None
    return {
        "running": bool(scheduler.running),
        "jobs": [
            {
                "id": job.id,
                "next_run_at": job.next_run_time and job.next_run_time.isoformat(),
                "trigger": str(job.trigger),
            }
            for job in sorted(scheduler.get_jobs(), key=lambda j: j.id)
        ],
    }


@router.get("/overview")
async def overview(request: Request) -> JSONResponse:
    runner: IngestRunner = request.app.state.ingest
    async with request.app.state.pool.acquire() as conn:
        quarantine = await conn.fetch(_QUARANTINE_SQL)
        flags = await conn.fetch(_FLAGS_SQL)
        cursor_errors = await conn.fetch(_CURSOR_ERRORS_SQL)
    return JSONResponse(
        {
            "generated_at": request.app.state.clock.now().isoformat(),
            "history_since": request.app.state.started_at.isoformat(),
            "job_running": runner.busy,
            "scheduler": _scheduler(request),
            "runs": [state.to_dict() for state in runner.history],
            "quarantine": [
                {"timeframe": r["timeframe"], "reason": r["reason"], "count": r["n"]}
                for r in quarantine
            ],
            "flags": [
                {"timeframe": r["timeframe"], "flag": r["flag"], "count": r["n"]} for r in flags
            ],
            "cursor_errors": [
                {
                    "grain": r["grain"],
                    "symbol": r["symbol"],
                    "error": r["last_error"],
                    "updated_at": r["updated_at"].isoformat(),
                }
                for r in cursor_errors
            ],
        }
    )
