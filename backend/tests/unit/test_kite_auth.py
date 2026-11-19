from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.broker.kite.auth import (
    KiteLoginError,
    KiteNotConfiguredError,
    checksum,
    exchange_request_token,
    login_url,
)
from app.broker.kite.session import KiteSession, session_expiry
from app.core.tradingdate import MARKET_TZ
from tests.conftest import SettingsFactory
from tests.fixtures.kite_http import KITE_KEY, KITE_SECRET, FakeKiteHTTP, kite_error, kite_ok

IST = MARKET_TZ


def test_checksum_matches_known_vector() -> None:
    # sha256("kitekey123" + "reqTOKEN456" + "kitesecret789"), computed independently
    assert checksum("kitekey123", "reqTOKEN456", "kitesecret789") == (
        "e25f6895e51a7e08d6ee86d6839a8eb43fcea43b30e5c3de8243d6633190e6ed"
    )


@pytest.mark.parametrize(
    ("login", "expected"),
    [
        (datetime(2026, 11, 19, 9, 15, tzinfo=IST), datetime(2026, 11, 20, 6, 0, tzinfo=IST)),
        (datetime(2026, 11, 19, 5, 59, tzinfo=IST), datetime(2026, 11, 19, 6, 0, tzinfo=IST)),
        (datetime(2026, 11, 19, 6, 0, tzinfo=IST), datetime(2026, 11, 20, 6, 0, tzinfo=IST)),
        # 01:00 UTC == 06:30 IST -> next morning
        (datetime(2026, 11, 19, 1, 0, tzinfo=UTC), datetime(2026, 11, 20, 6, 0, tzinfo=IST)),
    ],
)
def test_session_expires_at_next_6am_ist(login: datetime, expected: datetime) -> None:
    assert session_expiry(login, 6) == expected


def test_session_validity_boundary() -> None:
    s = KiteSession(
        "U1",
        "tok",
        datetime(2026, 11, 19, 9, 0, tzinfo=IST),
        datetime(2026, 11, 20, 6, 0, tzinfo=IST),
    )
    assert s.is_valid(datetime(2026, 11, 20, 5, 59, 59, tzinfo=IST))
    assert not s.is_valid(datetime(2026, 11, 20, 6, 0, tzinfo=IST))


def test_login_url_carries_key_and_state(make_settings: SettingsFactory) -> None:
    s = make_settings(kite_api_key=KITE_KEY, kite_api_secret=KITE_SECRET)
    url = urlparse(login_url(s, "nonce123"))
    q = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://kite.zerodha.com/connect/login"
    assert q["v"] == ["3"]
    assert q["api_key"] == [KITE_KEY]
    assert parse_qs(q["redirect_params"][0]) == {"state": ["nonce123"]}
    assert KITE_SECRET not in login_url(s, "x")


def test_not_configured_fails_closed(make_settings: SettingsFactory) -> None:
    with pytest.raises(KiteNotConfiguredError):
        login_url(make_settings(), "x")


async def _exchange(make_settings: SettingsFactory, fake: FakeKiteHTTP) -> KiteSession:
    s = make_settings(kite_api_key=KITE_KEY, kite_api_secret=KITE_SECRET)
    async with httpx.AsyncClient(transport=fake.transport()) as http:
        return await exchange_request_token(http, s, "reqTOKEN456")


async def test_exchange_success(make_settings: SettingsFactory) -> None:
    fake = FakeKiteHTTP()
    fake.on(
        "POST",
        "/session/token",
        kite_ok(
            {
                "user_id": "AB1234",
                "access_token": "acc_TOKEN_xyz",
                "login_time": "2026-11-19 09:15:00",
            }
        ),
    )
    session = await _exchange(make_settings, fake)
    assert session.user_id == "AB1234"
    assert session.access_token == "acc_TOKEN_xyz"
    assert session.login_time == datetime(2026, 11, 19, 9, 15, tzinfo=IST)
    assert session.expires_at == datetime(2026, 11, 20, 6, 0, tzinfo=IST)
    form = fake.form()
    assert form == {
        "api_key": KITE_KEY,
        "request_token": "reqTOKEN456",
        "checksum": "e25f6895e51a7e08d6ee86d6839a8eb43fcea43b30e5c3de8243d6633190e6ed",
    }
    assert fake.requests[0].headers["X-Kite-Version"] == "3"


@pytest.mark.parametrize(
    "response",
    [
        kite_error(403, "TokenException", "Token is invalid or has expired."),
        kite_error(500, "GeneralException"),
        kite_ok({"user_id": "AB1234"}),  # no access_token
        kite_ok({"user_id": "AB1234", "access_token": "t", "login_time": "garbage"}),
        httpx.Response(502, text="<html>bad gateway</html>"),
    ],
)
async def test_exchange_failures_raise(
    make_settings: SettingsFactory, response: httpx.Response
) -> None:
    fake = FakeKiteHTTP()
    fake.on("POST", "/session/token", response)
    with pytest.raises(KiteLoginError):
        await _exchange(make_settings, fake)


async def test_exchange_network_error_raises(make_settings: SettingsFactory) -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable")

    fake = FakeKiteHTTP()
    fake.on("POST", "/session/token", boom)
    with pytest.raises(KiteLoginError, match="ConnectError"):
        await _exchange(make_settings, fake)
