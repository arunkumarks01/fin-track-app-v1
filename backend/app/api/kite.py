"""Kite login endpoints (v1 flow, v2 storage + CSRF state).

GET  /api/kite/login-url  → Kite login URL carrying a one-time `state` nonce
GET  /api/kite/callback   → Kite redirects here; verifies state, exchanges the token
GET  /api/kite/status     → honest connection + expiry status
POST /api/kite/logout     → bearer-protected (middleware); clears local + remote session
"""

from __future__ import annotations

import html
import logging
import secrets
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.broker.kite.auth import (
    KiteLoginError,
    KiteNotConfiguredError,
    exchange_request_token,
    invalidate_remote,
    login_url,
)
from app.broker.kite.session import SessionStore
from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kite")

STATE_TTL_SECONDS = 600
_STATE_KEY = "kite:login_state:"


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _sessions(request: Request) -> SessionStore:
    store: SessionStore = request.app.state.kite_sessions
    return store


def _not_configured() -> JSONResponse:
    return JSONResponse(
        {"configured": False, "detail": "KITE_API_KEY / KITE_API_SECRET not set"},
        status_code=503,
    )


@router.get("/login-url")
async def get_login_url(request: Request) -> JSONResponse:
    settings = _settings(request)
    if not settings.kite_configured:
        return _not_configured()
    state = secrets.token_urlsafe(24)
    request.app.state.cache.setex(_STATE_KEY + state, STATE_TTL_SECONDS, True)
    return JSONResponse({"login_url": login_url(settings, state)})


def _page(title: str, message: str, ok: bool, status: int) -> HTMLResponse:
    colour = "#16a34a" if ok else "#dc2626"
    mark = "✓" if ok else "✕"
    body = (
        f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
        f"<body style='font-family:system-ui;background:#0a0a0a;color:#e5e5e5;padding:3rem'>"
        f"<h1 style='color:{colour}'>{mark} {html.escape(title)}</h1>"
        f"<p>{html.escape(message)}</p><p>You can close this tab.</p></body>"
    )
    return HTMLResponse(body, status_code=status)


@router.get("/callback")
async def callback(
    request: Request,
    request_token: str = "",
    status: str = "",
    state: str = "",
) -> HTMLResponse:
    settings = _settings(request)
    if not settings.kite_configured:
        return _page("Kite not configured", "Set KITE_API_KEY / KITE_API_SECRET.", False, 503)
    cache = request.app.state.cache
    if not state or not cache.delete(_STATE_KEY + state):
        log.warning("kite callback rejected: unknown or reused state")
        return _page(
            "Login rejected",
            "This login wasn't started from FinTrack (or the link expired). Start again.",
            False,
            400,
        )
    if status != "success" or not request_token:
        return _page("Login cancelled", f"Kite returned status {status!r}.", False, 400)
    try:
        session = await exchange_request_token(request.app.state.http, settings, request_token)
    except KiteLoginError as exc:
        log.warning("kite login failed: %s", exc)
        return _page("Login failed", "Kite rejected the token exchange. Try again.", False, 502)
    await _sessions(request).save(session)
    expires = session.expires_at.astimezone(MARKET_TZ).strftime("%d %b %H:%M IST")
    return _page("Kite connected", f"Logged in as {session.user_id}. Expires {expires}.", True, 200)


@router.get("/status")
async def kite_status(request: Request) -> JSONResponse:
    settings = _settings(request)
    if not settings.kite_configured:
        return JSONResponse({"configured": False, "connected": False, "status": "not_configured"})
    store = _sessions(request)
    now: datetime = request.app.state.clock.now()
    stored = store.peek()
    if stored is None:
        return JSONResponse(
            {
                "configured": True,
                "connected": False,
                "status": "disconnected",
                "message": "Not logged in to Kite.",
            }
        )
    expires_ist = stored.expires_at.astimezone(MARKET_TZ)
    if not stored.is_valid(now):
        return JSONResponse(
            {
                "configured": True,
                "connected": False,
                "status": "expired",
                "user_id": stored.user_id,
                "expires_at": expires_ist.isoformat(),
                "message": "Kite session expired. Log in again.",
            }
        )
    hours_left = (stored.expires_at - now).total_seconds() / 3600
    return JSONResponse(
        {
            "configured": True,
            "connected": True,
            "status": "connected",
            "user_id": stored.user_id,
            "expires_at": expires_ist.isoformat(),
            "hours_left": round(hours_left, 1),
            "message": f"Session expires in {int(hours_left)}h — log in again before "
            f"{settings.kite_session_expiry_hour_ist:02d}:00 IST.",
        }
    )


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    settings = _settings(request)
    store = _sessions(request)
    stored = store.peek()
    remote = False
    if stored is not None and settings.kite_configured:
        try:
            remote = await invalidate_remote(request.app.state.http, settings, stored.access_token)
        except KiteNotConfiguredError:
            remote = False
    await store.invalidate("user logout")
    return JSONResponse({"connected": False, "remote_invalidated": remote})
