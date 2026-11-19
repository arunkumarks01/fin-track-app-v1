from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.broker.kite.client import (
    CandleParseError,
    KiteAuthError,
    KiteClient,
    KiteRequestError,
    KiteUnavailableError,
    RateLimiter,
    parse_candle,
)
from app.broker.kite.session import KiteSession
from app.core.tradingdate import MARKET_TZ
from tests.conftest import SettingsFactory
from tests.fixtures.clock import FrozenClock
from tests.fixtures.kite_http import KITE_KEY, KITE_SECRET, FakeKiteHTTP, kite_error, kite_ok

IST = MARKET_TZ
NOW = datetime(2026, 11, 19, 10, 0, tzinfo=IST)
HIST = "/instruments/historical/256265/day"


class FakeSessions:
    def __init__(self, valid: bool = True) -> None:
        self.session: KiteSession | None = (
            KiteSession("AB1234", "acc_tok", NOW, NOW + timedelta(hours=20)) if valid else None
        )
        self.invalidated: list[str] = []

    def current(self) -> KiteSession | None:
        return self.session

    async def invalidate(self, reason: str) -> None:
        self.invalidated.append(reason)
        self.session = None


class Harness:
    def __init__(
        self,
        make_settings: SettingsFactory,
        fake: FakeKiteHTTP,
        sessions: FakeSessions | None = None,
        **overrides: object,
    ) -> None:
        self.clock = FrozenClock(NOW)
        self.sleeps: list[float] = []
        self.sessions = sessions or FakeSessions()
        self.fake = fake
        self.settings = make_settings(
            kite_api_key=KITE_KEY, kite_api_secret=KITE_SECRET, **overrides
        )

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)
            self.clock.advance(seconds)

        self.http = httpx.AsyncClient(transport=fake.transport())
        self.client = KiteClient(self.http, self.settings, self.sessions, self.clock, sleep)

    async def historical(self) -> list[object]:
        try:
            return await self.client.historical_raw(256265, "day", NOW - timedelta(days=5), NOW)
        finally:
            await self.http.aclose()


CANDLES = {"candles": [["2026-11-17T00:00:00+0530", 1699.85, 1700, 1698.1, 1699.8, 543]]}


async def test_request_shape_and_auth_header(make_settings: SettingsFactory) -> None:
    fake = FakeKiteHTTP()
    fake.on("GET", HIST, kite_ok(CANDLES))
    h = Harness(make_settings, fake)
    rows = await h.historical()
    req = fake.requests[0]
    assert req.headers["Authorization"] == f"token {KITE_KEY}:acc_tok"
    assert req.headers["X-Kite-Version"] == "3"
    assert req.url.params["from"] == "2026-11-14 10:00:00"
    assert req.url.params["to"] == "2026-11-19 10:00:00"
    assert rows == [
        [
            "2026-11-17T00:00:00+0530",
            Decimal("1699.85"),
            1700,
            Decimal("1698.1"),
            Decimal("1699.8"),
            543,
        ]
    ]


async def test_rate_limit_spaces_historical_calls(make_settings: SettingsFactory) -> None:
    clock = FrozenClock(NOW)
    sleeps: list[float] = []

    async def sleep(s: float) -> None:
        sleeps.append(s)
        clock.advance(s)

    limiter = RateLimiter(3.0, clock, sleep)
    start = clock.monotonic()
    for _ in range(7):
        await limiter.acquire()
    # 7 calls at 3/s: first immediate, then 1/3 s apart → 2.0 s total
    assert clock.monotonic() - start == pytest.approx(2.0)
    assert len(sleeps) == 6


async def test_retries_then_succeeds(make_settings: SettingsFactory) -> None:
    fake = FakeKiteHTTP()
    fake.on(
        "GET",
        HIST,
        kite_error(429, "NetworkException"),
        kite_error(503, "GeneralException"),
        kite_ok(CANDLES),
    )
    h = Harness(make_settings, fake, kite_retry_backoff_seconds=1.0)
    assert len(await h.historical()) == 1
    assert len(fake.requests) == 3
    assert [s for s in h.sleeps if s >= 1.0] == [1.0, 2.0]  # exponential backoff


async def test_retries_exhausted_fail_closed(make_settings: SettingsFactory) -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    fake = FakeKiteHTTP()
    fake.on("GET", HIST, boom)
    h = Harness(make_settings, fake, kite_max_retries=2)
    with pytest.raises(KiteUnavailableError, match="after 3 attempts: ReadTimeout"):
        await h.historical()
    assert len(fake.requests) == 3


async def test_token_exception_invalidates_and_stops(make_settings: SettingsFactory) -> None:
    fake = FakeKiteHTTP()
    fake.on("GET", HIST, kite_error(403, "TokenException", "Token expired"))
    h = Harness(make_settings, fake)
    with pytest.raises(KiteAuthError):
        await h.historical()
    assert len(fake.requests) == 1  # never retried
    assert h.sessions.invalidated
    assert h.sessions.current() is None


async def test_permission_403_keeps_session(make_settings: SettingsFactory) -> None:
    fake = FakeKiteHTTP()
    fake.on("GET", HIST, kite_error(403, "PermissionException", "No historical data add-on"))
    h = Harness(make_settings, fake)
    with pytest.raises(KiteRequestError, match="No historical data add-on"):
        await h.historical()
    assert h.sessions.invalidated == []
    assert len(fake.requests) == 1


async def test_input_error_not_retried(make_settings: SettingsFactory) -> None:
    fake = FakeKiteHTTP()
    fake.on("GET", HIST, kite_error(400, "InputException", "interval exceeds max limit"))
    h = Harness(make_settings, fake)
    with pytest.raises(KiteRequestError, match="InputException"):
        await h.historical()
    assert len(fake.requests) == 1


async def test_no_session_never_calls_kite(make_settings: SettingsFactory) -> None:
    fake = FakeKiteHTTP()
    h = Harness(make_settings, fake, sessions=FakeSessions(valid=False))
    with pytest.raises(KiteAuthError, match="log in"):
        await h.historical()
    assert fake.requests == []


async def test_unexpected_shape_rejected(make_settings: SettingsFactory) -> None:
    fake = FakeKiteHTTP()
    fake.on("GET", HIST, kite_ok({"rows": []}))
    with pytest.raises(KiteRequestError, match="shape"):
        await Harness(make_settings, fake).historical()


# --- candle parsing: exact paisa or quarantine ---
def test_parse_candle_exact_paisa() -> None:
    c = parse_candle(
        [
            "2026-11-17T00:00:00+0530",
            Decimal("1699.85"),
            1700,
            Decimal("1698.1"),
            Decimal("1699.8"),
            543,
            12,
        ]
    )
    assert (c.open, c.high, c.low, c.close, c.volume, c.oi) == (
        169985,
        170000,
        169810,
        169980,
        543,
        12,
    )
    assert c.ts == datetime(2026, 11, 17, tzinfo=IST)


@pytest.mark.parametrize(
    "record",
    [
        ["2026-11-17T00:00:00+0530", Decimal("1.005"), 2, 1, 1, 5],  # sub-paisa
        ["2026-11-17T00:00:00+0530", 1.5, 2, 1, 1, 5],  # float slipped through
        ["2026-11-17T00:00:00", 1, 2, 1, 1, 5],  # naive timestamp
        ["not-a-date", 1, 2, 1, 1, 5],
        ["2026-11-17T00:00:00+0530", 1, 2, 1, 1],  # too short
        ["2026-11-17T00:00:00+0530", 1, 2, 1, 1, True],  # bool volume
        "garbage",
    ],
)
def test_parse_candle_rejects(record: object) -> None:
    with pytest.raises(CandleParseError):
        parse_candle(record)
