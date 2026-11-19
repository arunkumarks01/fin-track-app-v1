"""Kite Connect OAuth (ported from v1; Redis replaced by SessionStore).

Flow: login URL (with a one-time `state` nonce in redirect_params) → user logs in at Kite →
Kite redirects to /api/kite/callback?request_token=…&state=… → we POST the request_token
plus checksum = SHA-256(api_key + request_token + api_secret) to /session/token.
Docs: https://kite.trade/docs/connect/v3/user/
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from app.broker.kite.session import KiteSession, session_expiry
from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ

log = logging.getLogger(__name__)

KITE_VERSION_HEADER = {"X-Kite-Version": "3"}


class KiteNotConfiguredError(RuntimeError):
    """KITE_API_KEY / KITE_API_SECRET missing — Kite features stay off."""


class KiteLoginError(RuntimeError):
    """Token exchange failed; no session is stored."""


def _credentials(settings: Settings) -> tuple[str, str]:
    if settings.kite_api_key is None or settings.kite_api_secret is None:
        raise KiteNotConfiguredError("KITE_API_KEY and KITE_API_SECRET must be set in .env")
    return settings.kite_api_key.get_secret_value(), settings.kite_api_secret.get_secret_value()


def checksum(api_key: str, request_token: str, api_secret: str) -> str:
    return hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()


def login_url(settings: Settings, state: str) -> str:
    api_key, _ = _credentials(settings)
    query = urlencode(
        {"v": "3", "api_key": api_key, "redirect_params": urlencode({"state": state})}
    )
    return f"{settings.kite_login_url}?{query}"


def _parse_login_time(raw: Any) -> datetime:
    """Kite returns login_time as naive IST 'YYYY-MM-DD HH:MM:SS'."""
    if not isinstance(raw, str):
        raise KiteLoginError("token response missing login_time")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise KiteLoginError(f"unparseable login_time {raw!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=MARKET_TZ)


async def exchange_request_token(
    http: httpx.AsyncClient, settings: Settings, request_token: str
) -> KiteSession:
    api_key, api_secret = _credentials(settings)
    if not request_token:
        raise KiteLoginError("empty request_token")
    try:
        resp = await http.post(
            f"{settings.kite_api_base_url}/session/token",
            data={
                "api_key": api_key,
                "request_token": request_token,
                "checksum": checksum(api_key, request_token, api_secret),
            },
            headers=KITE_VERSION_HEADER,
            timeout=settings.kite_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise KiteLoginError(f"token exchange request failed: {type(exc).__name__}") from exc
    try:
        body = resp.json(parse_float=Decimal)
    except ValueError as exc:
        raise KiteLoginError(
            f"token exchange: non-JSON response (HTTP {resp.status_code})"
        ) from exc
    if resp.status_code != 200 or body.get("status") != "success":
        raise KiteLoginError(
            f"token exchange failed: HTTP {resp.status_code} "
            f"{body.get('error_type', '')} {body.get('message', '')}".strip()
        )
    data = body.get("data") or {}
    token, user_id = data.get("access_token"), data.get("user_id")
    if not token or not user_id:
        raise KiteLoginError("token response missing access_token or user_id")
    login_time = _parse_login_time(data.get("login_time"))
    return KiteSession(
        user_id=str(user_id),
        access_token=str(token),
        login_time=login_time,
        expires_at=session_expiry(login_time, settings.kite_session_expiry_hour_ist),
    )


async def invalidate_remote(http: httpx.AsyncClient, settings: Settings, token: str) -> bool:
    """Best-effort DELETE /session/token. Local state is cleared regardless."""
    api_key, _ = _credentials(settings)
    try:
        resp = await http.delete(
            f"{settings.kite_api_base_url}/session/token",
            params={"api_key": api_key, "access_token": token},
            headers=KITE_VERSION_HEADER,
            timeout=settings.kite_timeout_seconds,
        )
    except httpx.HTTPError:
        log.warning("kite logout: remote invalidation request failed", exc_info=True)
        return False
    return resp.status_code == 200
