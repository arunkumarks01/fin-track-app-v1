"""Read-only Phase 2 research routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.analytics.chain import option_report
from app.analytics.technical import indicator_report
from app.data.candles import CandleRequestError, UnknownSymbol

router = APIRouter(prefix="/api/analytics")
RateBps = Annotated[int | None, Query(ge=-2000, le=5000)]
DividendBps = Annotated[int | None, Query(ge=0, le=5000)]


def _as_of(request: Request, selected: datetime | None) -> datetime:
    now: datetime = request.app.state.clock.now()
    if selected is None:
        return now
    if selected.tzinfo is None or selected > now or not 1900 <= selected.year < 9999:
        raise HTTPException(422, detail="timestamp must include a timezone and be a past instant")
    return selected


@router.get("/indicators")
async def indicators(
    request: Request,
    symbol: Annotated[str, Query(min_length=1, max_length=80)],
    interval: str = "D",
    count: Annotated[int, Query(ge=1)] = 1,
    before: datetime | None = None,
) -> JSONResponse:
    anchor = _as_of(request, before)
    try:
        async with (
            request.app.state.pool.acquire() as conn,
            conn.transaction(isolation="repeatable_read", readonly=True),
        ):
            body = await indicator_report(
                conn, symbol, interval, anchor, count, request.app.state.settings
            )
    except CandleRequestError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    except UnknownSymbol as exc:
        raise HTTPException(404, detail=f"{symbol} is not in the universe") from exc
    return JSONResponse(body)


@router.get("/options")
async def options(
    request: Request,
    underlying: Annotated[str, Query(min_length=1, max_length=80)],
    as_of: datetime | None = None,
    rate_bps: RateBps = None,
    dividend_bps: DividendBps = None,
) -> JSONResponse:
    anchor = _as_of(request, as_of)
    settings = request.app.state.settings
    async with (
        request.app.state.pool.acquire() as conn,
        conn.transaction(isolation="repeatable_read", readonly=True),
    ):
        body = await option_report(
            conn,
            underlying,
            anchor,
            rate_bps if rate_bps is not None else settings.analytics_option_rate_bps,
            dividend_bps if dividend_bps is not None else settings.analytics_option_dividend_bps,
            settings,
        )
    return JSONResponse(body)
