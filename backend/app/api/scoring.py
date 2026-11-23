"""Candidate explanations, strategy definitions and on-demand news research."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from app.api.analytics import _as_of
from app.data.candles import CandleRequestError, UnknownSymbol
from app.scoring.news_research import NewsResearch, stored_news
from app.scoring.reports import candidate_report, save_strategy, serialize_strategy, strategy_rows
from app.scoring.rules import BUILT_INS, Instrument, Mode, Strategy
from app.scoring.scans import scan_history

router = APIRouter(prefix="/api/scoring")
Symbol = Annotated[str, Query(pattern=r"^[A-Za-z]+:[A-Za-z0-9 .&_-]{1,64}$")]


class StrategyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    definition: Strategy


@router.get("/candidates")
async def candidates(
    request: Request,
    symbol: Symbol,
    mode: Mode = "swing",
    instrument: Instrument = "underlying",
    as_of: datetime | None = None,
) -> dict[str, Any]:
    anchor = _as_of(request, as_of)
    symbol = symbol.upper()
    settings = request.app.state.settings
    research: NewsResearch = request.app.state.scoring_news
    try:
        async with (
            request.app.state.pool.acquire() as conn,
            conn.transaction(isolation="repeatable_read", readonly=True),
        ):
            result = await candidate_report(
                conn, symbol, mode, anchor, settings, instrument, include_trade_plans=True
            )
            sources = (
                await stored_news(conn, symbol, anchor, settings.scoring)
                if settings.scoring_news_enabled
                else []
            )
    except CandleRequestError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    except UnknownSymbol as exc:
        raise HTTPException(404, detail="Symbol is not in the universe") from exc
    result["news"] = (
        research.status(symbol, sources)
        if settings.scoring_news_enabled
        else {
            "status": "unknown",
            "reason": "News provider is disabled",
        }
    )
    return result


@router.get("/strategies")
async def strategies(request: Request) -> dict[str, Any]:
    async with request.app.state.pool.acquire() as conn:
        custom = await strategy_rows(conn, request.app.state.clock.now())
    return {
        "built_in": [item.model_dump() for item in BUILT_INS],
        "custom": [serialize_strategy(row) for row in custom],
    }


@router.put("/strategies", status_code=201)
async def store_strategy(request: Request, change: StrategyInput) -> dict[str, int]:
    try:
        async with request.app.state.pool.acquire() as conn:
            revision = await save_strategy(
                conn, change.definition, change.enabled, request.app.state.clock.now()
            )
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    if revision is None:
        raise HTTPException(409, detail="Use the next revision of this strategy")
    return {"revision": revision}


@router.post("/news")
async def research_news(request: Request, symbol: Symbol) -> dict[str, Any]:
    settings = request.app.state.settings
    if not settings.scoring_news_enabled:
        return {"status": "unknown", "reason": "News provider is disabled"}
    async with request.app.state.pool.acquire() as conn:
        sources = await stored_news(
            conn, symbol.upper(), request.app.state.clock.now(), settings.scoring
        )
    result: dict[str, Any] = await request.app.state.scoring_news.review(symbol.upper(), sources)
    return result


@router.get("/scans")
async def scan(request: Request, mode: Mode = "swing") -> dict[str, Any]:
    async with request.app.state.pool.acquire() as conn:
        history = await scan_history(conn, mode)
    return {
        "enabled": request.app.state.settings.scoring_scan_enabled,
        "latest": history[0] if history else None,
        "history": history,
    }
