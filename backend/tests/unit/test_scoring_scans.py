from __future__ import annotations

from datetime import datetime

import pytest

from app.core.tradingdate import MARKET_TZ
from app.scoring.scans import scan_universe
from tests.conftest import build_settings


@pytest.mark.asyncio
async def test_intraday_scan_waits_for_finalized_bars() -> None:
    settings = build_settings()
    result = await scan_universe(
        None, settings, datetime(2026, 11, 23, 15, 59, tzinfo=MARKET_TZ), "intraday"
    )
    assert result == {
        "skipped": "Awaiting finalized intraday bars",
        "stopped_reason": "Awaiting finalized intraday bars",
    }
