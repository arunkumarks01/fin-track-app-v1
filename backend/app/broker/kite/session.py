"""Kite session state: one hot copy in process memory, one durable row in Postgres.

Single process (law 5), so the in-memory copy can't drift across workers — v1's Redis
hazard is gone. Fail closed: an expired or missing session is reported as "no session",
never as a usable token.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Protocol

from app.core.clock import Clock
from app.core.logging import SECRETS
from app.core.tradingdate import MARKET_TZ

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class KiteSession:
    user_id: str
    access_token: str
    login_time: datetime  # aware
    expires_at: datetime  # aware

    def is_valid(self, now: datetime) -> bool:
        return now < self.expires_at


def session_expiry(login_time: datetime, expiry_hour_ist: int) -> datetime:
    """Kite tokens expire at the first `expiry_hour_ist`:00 IST strictly after login."""
    if login_time.tzinfo is None:
        raise ValueError("login_time must be timezone-aware")
    local = login_time.astimezone(MARKET_TZ)
    candidate = datetime.combine(local.date(), time(expiry_hour_ist), tzinfo=MARKET_TZ)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate


class SessionProvider(Protocol):
    def current(self) -> KiteSession | None: ...
    async def invalidate(self, reason: str) -> None: ...


class SessionStore:
    """The app's SessionProvider: memory first, Postgres for restart recovery."""

    def __init__(self, pool: Any, clock: Clock) -> None:
        self._pool = pool
        self._clock = clock
        self._session: KiteSession | None = None

    def current(self) -> KiteSession | None:
        s = self._session
        if s is None or not s.is_valid(self._clock.now()):
            return None
        return s

    def peek(self) -> KiteSession | None:
        """The stored session even if expired (for honest status reporting)."""
        return self._session

    async def load(self) -> KiteSession | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT user_id, access_token, login_time, expires_at FROM app.kite_session"
            )
        if row is None:
            self._session = None
            return None
        self._session = KiteSession(
            row["user_id"], row["access_token"], row["login_time"], row["expires_at"]
        )
        SECRETS.register([self._session.access_token])
        return self.current()

    async def save(self, session: KiteSession) -> None:
        SECRETS.register([session.access_token])
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO app.kite_session (id, user_id, access_token, login_time, expires_at)
                VALUES (1, $1, $2, $3, $4)
                ON CONFLICT (id) DO UPDATE SET user_id = EXCLUDED.user_id,
                    access_token = EXCLUDED.access_token, login_time = EXCLUDED.login_time,
                    expires_at = EXCLUDED.expires_at, stored_at = now()
                """,
                session.user_id,
                session.access_token,
                session.login_time,
                session.expires_at,
            )
        self._session = session
        log.info("kite session stored for user %s, expires %s", session.user_id, session.expires_at)

    async def invalidate(self, reason: str) -> None:
        self._session = None
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM app.kite_session")
        log.warning("kite session invalidated: %s", reason)
