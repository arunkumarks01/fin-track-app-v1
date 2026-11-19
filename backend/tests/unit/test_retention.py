from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.db.retention import TICKS_TABLE, drop_expired_chunks
from tests.conftest import SettingsFactory


async def test_naive_cutoff_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        await drop_expired_chunks(None, TICKS_TABLE, datetime(2024, 1, 1))  # noqa: DTZ001


def test_retention_cron_validated(make_settings: SettingsFactory) -> None:
    assert make_settings(ticks_retention_cron="5 3 * * 1-5").ticks_retention_cron == "5 3 * * 1-5"
    with pytest.raises(ValidationError, match="invalid crontab"):
        make_settings(ticks_retention_cron="every night")
