"""Quality-labelled source data remains separate from the clean participant table."""

from datetime import date, datetime
from typing import Any

import httpx
import pytest

from app.core.tradingdate import MARKET_TZ
from app.data.participant_assessment import assess_imported
from app.data.participant_exact import import_archived
from app.data.participant_review import retain_participant_source
from app.db.migrate import migrate
from app.main import create_app
from tests.conftest import build_settings
from tests.fixtures.clock import FrozenClock
from tests.unit.test_nse_reference import VOL_2015

pytestmark = pytest.mark.db
NOW = datetime(2026, 11, 21, 10, tzinfo=MARKET_TZ)


async def test_labelled_research_values_and_unverified_gaps(db: Any, test_dsn: str) -> None:
    await migrate(db)
    cases = [
        (date(2015, 1, 5), VOL_2015.replace("290814,253340", "290814.3333,253340", 1)),
        (
            date(2015, 1, 6),
            VOL_2015.replace("Jan 05, 2015", "Jan 06, 2015")
            .replace("2515645", "2515655", 1)
            .replace("6436292,6436292", "6436302,6436292", 1),
        ),
        (date(2015, 1, 7), VOL_2015.replace("Jan 05, 2015", "Jan 07")),
    ]
    for day, text in cases:
        await db.execute(
            "INSERT INTO app.reference_files(dataset,trade_date,status,detail,fetched_at) "
            "VALUES ('participant_vol',$1,'error','source issue',$2)",
            day,
            NOW,
        )
        await retain_participant_source(
            db,
            "participant_vol",
            day,
            "https://example.test/file.csv",
            text.encode(),
            "source issue",
            NOW,
        )
    await db.execute(
        "INSERT INTO app.reference_files(dataset,trade_date,status,detail,fetched_at) "
        "VALUES ('participant_oi',$1,'missing','404 from the archive',$2)",
        date(2015, 1, 8),
        NOW,
    )
    result = await import_archived(db, NOW)
    assert result["inserted"] == 3
    report = await assess_imported(db, 2, NOW)
    assert report["outcomes"] == {
        "reconciled_within_tolerance": 1,
        "component_totals_disagree": 1,
        "source_date_unverified": 1,
    }
    app = create_app(build_settings(database_url=test_dsn), clock=FrozenClock(NOW))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        response = await c.get("/api/data/participant-reviewed")
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "labelled_research_only"
    assert len(body["files"]) == 2
    assert body["gaps"] == [
        {
            "dataset": "participant_vol",
            "candidate_date": "2015-01-07",
            "reason": "source_date_unverified",
        },
        {
            "dataset": "participant_oi",
            "candidate_date": "2015-01-08",
            "reason": "missing_or_mislabeled_source",
        },
    ]
    first, second = body["files"]
    client = next(r for r in first["participants"] if r["participant"] == "Client")
    assert client["components"]["fut_idx_long"] == "290814.3333"
    assert first["quality"] == "reconciled_within_tolerance"
    assert second["quality"] == "component_totals_disagree"
    client2 = next(r for r in second["participants"] if r["participant"] == "Client")
    assert client2["reported_totals"]["long"] == "2515655"
    assert client2["derived_totals"]["long"] == "2515645"
    assert await db.fetchval("SELECT count(*) FROM app.participant_positions") == 0
