"""Paper replay routes. The existing bearer gate protects every state change."""

from __future__ import annotations

from datetime import date
from typing import Any, Self
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.risk.parameters import PaperMode
from app.sandbox import store
from app.sandbox.model import Instruction

router = APIRouter(prefix="/api/paper")


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=60)
    start_day: date
    mode: PaperMode


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    instruction: Instruction


class StepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_day: date | None


class ControlsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    paused: bool | None = None
    killed: bool | None = None

    @model_validator(mode="after")
    def any_control(self) -> Self:
        if self.paused is None and self.killed is None:
            raise ValueError("Choose a control to update")
        return self


@router.get("/accounts")
async def accounts(request: Request) -> dict[str, Any]:
    async with request.app.state.pool.acquire() as conn:
        return await store.listing(conn, request.app.state.clock.now(), request.app.state.settings)


@router.post("/accounts", status_code=201)
async def create_account(request: Request, data: AccountInput) -> dict[str, Any]:
    try:
        async with request.app.state.pool.acquire() as conn:
            return await store.create(
                conn,
                data.name,
                data.start_day,
                data.mode,
                request.app.state.clock.now(),
                request.app.state.settings,
            )
    except store.PaperError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc


@router.get("/accounts/{account_id}")
async def account(request: Request, account_id: int) -> dict[str, Any]:
    try:
        async with (
            request.app.state.pool.acquire() as conn,
            conn.transaction(isolation="repeatable_read", readonly=True),
        ):
            return await store.overview(conn, account_id)
    except store.PaperError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc


@router.post("/accounts/{account_id}/orders", status_code=201)
async def submit(request: Request, account_id: int, order: OrderInput) -> dict[str, Any]:
    try:
        async with request.app.state.pool.acquire() as conn:
            return await store.submit(
                conn,
                account_id,
                order.request_id,
                order.instruction,
                request.app.state.clock.now(),
            )
    except store.PaperError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc


@router.post("/accounts/{account_id}/orders/{order_id}/cancel")
async def cancel(request: Request, account_id: int, order_id: int) -> dict[str, bool]:
    try:
        async with request.app.state.pool.acquire() as conn:
            await store.cancel(conn, account_id, order_id)
    except store.PaperError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc
    return {"cancelled": True}


@router.post("/accounts/{account_id}/advance")
async def advance(request: Request, account_id: int, data: StepInput) -> dict[str, Any]:
    try:
        async with request.app.state.pool.acquire() as conn:
            return await store.advance(
                conn,
                account_id,
                data.expected_day,
                request.app.state.clock.now(),
                request.app.state.settings,
            )
    except store.PaperError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc


@router.put("/controls")
async def update_controls(request: Request, data: ControlsInput) -> dict[str, bool]:
    async with request.app.state.pool.acquire() as conn:
        state = await conn.fetchrow(
            """UPDATE app.paper_controls SET paused=coalesce($1,paused),
                   killed=coalesce($2,killed) WHERE id RETURNING paused,killed""",
            data.paused,
            data.killed,
        )
    return dict(state)
