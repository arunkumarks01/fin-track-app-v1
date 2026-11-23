"""Async Kite REST client (httpx direct — DEC-K2).

- Per-endpoint-class token-bucket rate limits from config (historical 3/s, others 10/s).
- Retries with exponential backoff on 429 / 5xx / network errors, up to KITE_MAX_RETRIES.
- Fail closed: TokenException (403) invalidates the session and raises KiteAuthError,
  which must stop ingestion. Input errors are not retried.
- Prices are parsed as exact Decimals from the JSON text and converted to integer paisa —
  a float never touches a price.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from app.broker.kite.auth import KITE_VERSION_HEADER
from app.broker.kite.session import SessionProvider
from app.core.clock import Clock
from app.core.config import Settings
from app.core.money import rupees_to_paisa

log = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]


class KiteError(RuntimeError):
    pass


class KiteAuthError(KiteError):
    """No valid session (not logged in, expired, or TokenException). Stop and re-login."""


class KiteRequestError(KiteError):
    """Kite rejected the request (input/permission); retrying won't help."""


class KiteUnavailableError(KiteError):
    """Retries exhausted on 429/5xx/network errors."""


class RateLimiter:
    """Token bucket on the injected clock: at most `rate` acquisitions per second."""

    def __init__(self, rate: float, clock: Clock, sleep: Sleep) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self._interval = 1.0 / rate
        self._clock = clock
        self._sleep = sleep
        self._next_free = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock.monotonic()
            wait = self._next_free - now
            if wait > 0:
                await self._sleep(wait)
                now = self._clock.monotonic()
            self._next_free = max(now, self._next_free) + self._interval


@dataclass(frozen=True)
class RawCandle:
    """One historical record exactly as Kite sent it, prices already in exact paisa."""

    ts: datetime
    open: int
    high: int
    low: int
    close: int
    volume: int
    oi: int | None
    raw: list[Any]


class CandleParseError(ValueError):
    def __init__(self, raw: Any, reason: str) -> None:
        super().__init__(reason)
        self.raw = raw


def parse_candle(record: Any) -> RawCandle:
    """[ts, o, h, l, c, v(, oi)] → RawCandle. Raises CandleParseError (→ quarantine)."""
    if not isinstance(record, list) or len(record) not in (6, 7):
        raise CandleParseError(record, "expected a 6- or 7-element array")
    ts_raw, *prices, volume = record[:6]
    try:
        ts = datetime.fromisoformat(str(ts_raw))
    except ValueError as exc:
        raise CandleParseError(record, f"bad timestamp {ts_raw!r}") from exc
    if ts.tzinfo is None:
        raise CandleParseError(record, "timestamp without timezone")
    try:
        o, h, lo, c = (rupees_to_paisa(p) for p in prices)
    except (TypeError, ValueError) as exc:
        raise CandleParseError(record, f"bad price: {exc}") from exc
    if not isinstance(volume, int) or isinstance(volume, bool):
        raise CandleParseError(record, f"bad volume {volume!r}")
    oi = record[6] if len(record) == 7 else None
    if oi is not None and (not isinstance(oi, int) or isinstance(oi, bool)):
        raise CandleParseError(record, f"bad oi {oi!r}")
    return RawCandle(ts, o, h, lo, c, volume, oi, list(record))


def _kite_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class KiteClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        settings: Settings,
        sessions: SessionProvider,
        clock: Clock,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if settings.kite_api_key is None:
            raise KiteAuthError("KITE_API_KEY not configured")
        self._http = http
        self._s = settings
        self._api_key = settings.kite_api_key.get_secret_value()
        self._sessions = sessions
        self._sleep = sleep
        self.historical_limiter = RateLimiter(settings.kite_historical_rps, clock, sleep)
        self.default_limiter = RateLimiter(settings.kite_default_rps, clock, sleep)
        self.quote_limiter = RateLimiter(settings.kite_quote_rps, clock, sleep)

    def _headers(self) -> dict[str, str]:
        session = self._sessions.current()
        if session is None:
            raise KiteAuthError("no valid Kite session — log in via /api/kite/login-url")
        return {
            **KITE_VERSION_HEADER,
            "Authorization": f"token {self._api_key}:{session.access_token}",
        }

    async def _get(
        self,
        path: str,
        limiter: RateLimiter,
        params: dict[str, str] | list[tuple[str, str | int | float | bool | None]] | None = None,
    ) -> httpx.Response:
        attempts = self._s.kite_max_retries + 1
        last: str = ""
        for attempt in range(attempts):
            headers = self._headers()  # re-check the session on every attempt
            await limiter.acquire()
            try:
                resp = await self._http.get(
                    f"{self._s.kite_api_base_url}{path}",
                    params=params,
                    headers=headers,
                    timeout=self._s.kite_timeout_seconds,
                )
            except httpx.TransportError as exc:
                last = f"{type(exc).__name__}"
            else:
                if resp.status_code == 200:
                    return resp
                error_type = _error_type(resp)
                # only TokenException means the session is dead; other 403s (e.g. a
                # PermissionException for a missing historical-data add-on) are surfaced as-is
                if error_type == "TokenException":
                    await self._sessions.invalidate(f"Kite {resp.status_code} {error_type}")
                    raise KiteAuthError(f"Kite session rejected ({error_type or resp.status_code})")
                if resp.status_code != 429 and resp.status_code < 500:
                    raise KiteRequestError(
                        f"Kite {resp.status_code} {error_type}: {_message(resp)}"[:300]
                    )
                last = f"HTTP {resp.status_code} {error_type}".strip()
            if attempt < attempts - 1:
                delay = self._s.kite_retry_backoff_seconds * (2**attempt)
                log.warning(
                    "kite GET %s failed (%s); retry %d in %.1fs", path, last, attempt + 1, delay
                )
                await self._sleep(delay)
        raise KiteUnavailableError(f"Kite GET {path} failed after {attempts} attempts: {last}")

    async def instruments_csv(self) -> str:
        resp = await self._get("/instruments", self.default_limiter)
        return resp.text

    async def historical_raw(
        self,
        instrument_token: int,
        interval: str,
        start: datetime,
        end: datetime,
        *,
        oi: bool = False,
        continuous: bool = False,
    ) -> list[Any]:
        """Raw candle records for one request window (caller chunks to the window limit)."""
        if interval not in self._s.kite_window_days:
            raise ValueError(f"unknown Kite interval {interval!r}")
        params = {
            "from": _kite_ts(start),
            "to": _kite_ts(end),
            "oi": "1" if oi else "0",
            "continuous": "1" if continuous else "0",
        }
        resp = await self._get(
            f"/instruments/historical/{instrument_token}/{interval}",
            self.historical_limiter,
            params,
        )
        try:
            body = resp.json(parse_float=Decimal)
            candles = body["data"]["candles"]
        except (ValueError, KeyError, TypeError) as exc:
            raise KiteRequestError("historical: unexpected response shape") from exc
        if not isinstance(candles, list):
            raise KiteRequestError("historical: candles is not a list")
        return candles

    async def quote(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        """Full quotes for `EXCHANGE:TRADINGSYMBOL` keys (Kite: at most 500 per request, 1 req/s).
        Keys Kite has no data for are simply absent from the result. Prices are exact Decimals."""
        if not keys or len(keys) > 500:
            raise ValueError("quote needs 1..500 instrument keys")
        pairs: list[tuple[str, str | int | float | bool | None]] = [("i", k) for k in keys]
        resp = await self._get("/quote", self.quote_limiter, pairs)
        try:
            data = resp.json(parse_float=Decimal)["data"]
        except (ValueError, KeyError, TypeError) as exc:
            raise KiteRequestError("quote: unexpected response shape") from exc
        if not isinstance(data, dict):
            raise KiteRequestError("quote: data is not a mapping")
        return data


def _error_type(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("error_type", ""))
    except (ValueError, AttributeError):
        return ""


def _message(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("message", ""))
    except (ValueError, AttributeError):
        return resp.text[:200]
