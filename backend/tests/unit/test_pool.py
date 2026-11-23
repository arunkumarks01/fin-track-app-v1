"""Pool startup must apply database-side limits to all application connections."""

from typing import Any

import asyncpg
import pytest
from pydantic import ValidationError

from app.db import pool
from tests.conftest import SettingsFactory


async def test_pool_passes_configured_timeouts(
    monkeypatch: pytest.MonkeyPatch, make_settings: SettingsFactory
) -> None:
    captured: dict[str, Any] = {}

    async def fake_create_pool(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
    settings = make_settings(
        db_statement_timeout_ms=1234,
        db_lock_timeout_ms=567,
        db_idle_transaction_timeout_ms=8901,
    )
    await pool.create_pool(settings)
    assert captured["server_settings"]["statement_timeout"] == "1234"
    assert captured["server_settings"]["lock_timeout"] == "567"
    assert captured["server_settings"]["idle_in_transaction_session_timeout"] == "8901"


@pytest.mark.parametrize(
    "name",
    ["db_statement_timeout_ms", "db_lock_timeout_ms", "db_idle_transaction_timeout_ms"],
)
def test_pool_rejects_disabled_timeouts(make_settings: SettingsFactory, name: str) -> None:
    with pytest.raises(ValidationError, match=name):
        make_settings(**{name: 0})
