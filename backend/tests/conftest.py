"""Shared, deterministic fixtures. No test reads the developer's `.env` implicitly."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.core.config import Settings
from app.core.logging import SECRETS
from tests.fixtures.clock import FrozenClock
from tests.fixtures.kite import FakeKite
from tests.fixtures.llm import FakeLLM

IST = ZoneInfo("Asia/Kolkata")
TEST_TOKEN = "t" * 40
# Fixed instant: 2024-01-15 09:15 IST (a Monday, market open)
FROZEN_START = datetime(2024, 1, 15, 9, 15, tzinfo=IST)

SettingsFactory = Callable[..., Settings]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--snapshot-update",
        action="store_true",
        help="Regenerate reviewed regression snapshot baselines",
    )


def build_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "api_auth_token": TEST_TOKEN,
        "database_url": "postgresql://u:p@localhost:5432/unused_test",
        # the daily job's NSE-archive + reference chains are exercised by their own tests only
        "nse_archive_after_daily": False,
        "reference_after_daily": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def make_settings() -> SettingsFactory:
    return build_settings


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(FROZEN_START)


@pytest.fixture
def fake_kite() -> FakeKite:
    return FakeKite()


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture(autouse=True)
def _isolate_secret_registry() -> Iterator[None]:
    SECRETS.clear()
    yield
    SECRETS.clear()
