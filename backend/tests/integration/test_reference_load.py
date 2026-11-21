"""Phase 1e step 1: delivery % + participant positions loaded from a fake NSE archive."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Any

import asyncpg
import httpx
import pytest

from app.broker.kite.client import RateLimiter
from app.core.tradingdate import MARKET_TZ, canonical_daily_ts
from app.data.instruments import UniverseMember, sync_universe
from app.data.nse_archive import NseArchiveClient
from app.data.reference_load import run_reference_load
from app.db.migrate import migrate
from app.main import create_app
from tests.conftest import build_settings
from tests.fixtures.clock import FrozenClock
from tests.unit.test_nse_reference import MCAP_2026, OI, VOL_2015

pytestmark = pytest.mark.db

IST = MARKET_TZ
NOW = datetime(2026, 11, 21, 9, 0, tzinfo=IST)
OLD = date(2022, 12, 19)  # SHRIRAMFIN still traded as SRTRANSFIN
D19 = date(2026, 11, 19)
D20 = date(2026, 11, 20)
DAYS = [OLD, D19, D20]
STEP1 = ["mto", "participant_oi", "participant_vol"]

BULK = (
    "Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,"
    "Trade Price / Wght. Avg. Price,Remarks\n"
    "20-NOV-2026,RELIANCE,Reliance Industries Limited,BIG FUND,BUY,500000,1290.155,-\n"
)
BLOCK = (
    "Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,"
    "Trade Price / Wght. Avg. Price\n"
)


def pr_zip(d: date) -> bytes:
    import io
    import zipfile

    name = "SRTRANSFIN" if d < date(2022, 12, 20) else "SHRIRAMFIN"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            f"an{d:%d%m%Y}.txt",
            "COMPANY NAME    SYMBOL    : ANNOUNCEMENTS\n"
            f"Shriram Finance Limited {name} : Press Release {name} : Results declared\n"
            "Other Company Limited OTHERCO : Updates OTHERCO : not ours\n",
        )
        z.writestr(
            f"bm{d:%d%m%Y}.txt",
            "COMPANY NAME    SYMBOL     : BM DATE    : BM PURPOSE\n"
            "Reliance Industries Limited RELIANCE : 15-JAN-2027 : Financial Results Q3\n",
        )
        if d == D20:
            z.writestr(f"mcap{d:%d%m%Y}.csv", MCAP_2026)
    return buf.getvalue()


def mto(d: date, stated: date | None = None) -> str:
    s = stated or d
    name = "SRTRANSFIN" if d < date(2022, 12, 20) else "SHRIRAMFIN"
    return (
        "Security Wise Delivery Position - Compulsory Rolling Settlement\n"
        f"10,MTO,{s:%d%m%Y},1,1\n"
        "Record Type,Sr No,Name of Security,Quantity Traded,Deliverable Quantity,%\n"
        "20,1,RELIANCE,EQ,1000,400,40.00\n"
        f"20,2,{name},EQ,2000,1500,75.00\n"
        f"20,3,{name},N5,10,10,100.00\n"
        "20,4,OTHERCO,EQ,5,5,100.00\n"
    )


def participants(kind: str, d: date) -> str:
    text = OI if kind == "oi" else VOL_2015
    stated = "Nov 20, 2026" if kind == "oi" else "Jan 05, 2015"
    return text.replace(stated, f"{d:%b} {d.day:02d}, {d.year}")


def _server(
    overrides: dict[str, httpx.Response] | None = None,
) -> tuple[httpx.MockTransport, list[str]]:
    seen: list[str] = []
    overrides = overrides or {}

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        seen.append(url)
        name = url.rsplit("/", 1)[-1]
        if name in overrides:
            return overrides[name]
        if name == "bulk.csv":
            return httpx.Response(200, text=BULK)
        if name == "block.csv":
            return httpx.Response(200, text=BLOCK)
        if name.startswith("PR") and name.endswith(".zip"):
            return httpx.Response(
                200,
                content=pr_zip(datetime.strptime(name[2:8], "%d%m%y").date()),  # noqa: DTZ007
            )
        digits = name.rsplit("_", 1)[-1].split(".")[0]
        d = datetime.strptime(digits, "%d%m%Y").date()  # noqa: DTZ007
        if name.startswith("MTO_"):
            return httpx.Response(200, text=mto(d))
        kind = "oi" if "_oi_" in name else "volume"
        return httpx.Response(200, text=participants(kind, d))

    return httpx.MockTransport(handler), seen


@pytest.fixture
async def pool(test_dsn: str, db: Any) -> AsyncIterator[Any]:
    p = await asyncpg.create_pool(test_dsn, min_size=1, max_size=3)
    try:
        yield p
    finally:
        await p.close()


async def _no_sleep(_s: float) -> None:
    return None


def _client(http: httpx.AsyncClient, settings: Any) -> NseArchiveClient:
    return NseArchiveClient(
        http, settings, RateLimiter(1000.0, FrozenClock(NOW), _no_sleep), _no_sleep
    )


async def _seed(db: Any) -> None:
    await migrate(db)
    await sync_universe(
        db,
        [
            UniverseMember("NSE:RELIANCE", 1, "equity"),
            UniverseMember("NSE:SHRIRAMFIN", 2, "equity"),
            UniverseMember("NSE:NIFTY 50", 3, "index"),
        ],
        NOW,
    )
    for d in DAYS:
        await db.execute(
            """INSERT INTO app.ohlcv_candles (symbol, timeframe, ts, open, high, low, close, volume)
               VALUES ('NSE:NIFTY 50', '1d', $1, 100, 100, 100, 100, 0)""",
            canonical_daily_ts(d, IST),
        )
    await db.execute(
        """INSERT INTO app.symbol_changes (old_symbol, new_symbol, effective_date, company)
           VALUES ('SRTRANSFIN', 'SHRIRAMFIN', '2022-12-20', 'Shriram Finance Limited')"""
    )


def _settings(test_dsn: str, **kw: Any) -> Any:
    return build_settings(database_url=test_dsn, nse_archive_start=date(2022, 1, 1), **kw)


async def test_loads_three_datasets_resumably_with_gaps_and_refusals_recorded(
    pool: Any, db: Any, test_dsn: str
) -> None:
    await _seed(db)
    settings = _settings(test_dsn)
    bad_total = participants("oi", OLD).replace("TOTAL,336976", "TOTAL,336986")  # off by 10
    transport, seen = _server(
        {
            f"MTO_{D19:%d%m%Y}.DAT": httpx.Response(200, text=mto(D19, stated=D20)),  # mislabelled
            f"fao_participant_vol_{D19:%d%m%Y}.csv": httpx.Response(404),
            f"fao_participant_oi_{OLD:%d%m%Y}.csv": httpx.Response(200, text=bad_total),
        }
    )
    async with httpx.AsyncClient(transport=transport) as http:
        report = await run_reference_load(
            pool, _client(http, settings), settings, FrozenClock(NOW), D20, datasets=STEP1
        )
        assert report.todo == {"mto": 3, "participant_oi": 3, "participant_vol": 3}
        assert (report.ok, report.missing, report.errors) == (6, 2, 1)
        assert "20112026" in seen[0]  # newest first
        # the real NSE OI rounding (off by 1) is stored and counted, not refused
        assert report.rounding_days == 2

        ledger = {
            (r["dataset"], r["trade_date"]): (r["status"], r["detail"])
            for r in await db.fetch(
                "SELECT dataset, trade_date, status, detail FROM app.reference_files"
            )
        }
        assert ledger[("mto", D19)][0] == "missing"  # wrong day's file: a gap, never stored
        assert ledger[("participant_vol", D19)] == ("missing", "404 from the archive")
        assert ledger[("participant_oi", OLD)][0] == "error"
        assert "nse_total_gap_contracts" in (ledger[("participant_oi", D20)][1] or "")

        delivery = await db.fetch(
            "SELECT symbol, trade_date, deliv_bps, source_symbol FROM app.delivery_daily "
            "ORDER BY 2, 1"
        )
        assert [
            (r["symbol"], r["trade_date"], r["deliv_bps"], r["source_symbol"]) for r in delivery
        ] == [
            ("NSE:RELIANCE", OLD, 4000, None),
            ("NSE:SHRIRAMFIN", OLD, 7500, "SRTRANSFIN"),  # old name mapped, its bonds skipped
            ("NSE:RELIANCE", D20, 4000, None),
            ("NSE:SHRIRAMFIN", D20, 7500, None),
        ]
        assert await db.fetchval("SELECT count(*) FROM app.participant_positions") == 4 * 4

        # resumable: only the error is retried; gaps wait for retry_missing
        seen.clear()
        again = await run_reference_load(
            pool, _client(http, settings), settings, FrozenClock(NOW), D20, datasets=STEP1
        )
        assert again.todo == {"mto": 0, "participant_oi": 1, "participant_vol": 0}
        assert len(seen) == 1


async def test_a_refusal_stops_the_whole_load(pool: Any, db: Any, test_dsn: str) -> None:
    await _seed(db)
    settings = _settings(test_dsn)

    def refuse(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as http:
        report = await run_reference_load(
            pool, _client(http, settings), settings, FrozenClock(NOW), D20
        )
    assert report.stopped_reason is not None
    assert "refused" in report.stopped_reason
    assert report.done == 0
    assert await db.fetchval("SELECT count(*) FROM app.reference_files") == 0


async def test_delivery_needs_the_name_map(pool: Any, db: Any, test_dsn: str) -> None:
    await _seed(db)
    await db.execute("DELETE FROM app.symbol_changes")
    settings = _settings(test_dsn)
    transport, seen = _server()
    async with httpx.AsyncClient(transport=transport) as http:
        report = await run_reference_load(
            pool, _client(http, settings), settings, FrozenClock(NOW), D20, datasets=["mto"]
        )
    assert report.stopped_reason is not None
    assert "symbol-change" in report.stopped_reason
    assert seen == []


async def test_reference_and_stock_endpoints_show_what_was_loaded(
    pool: Any, db: Any, test_dsn: str
) -> None:
    await _seed(db)
    settings = _settings(test_dsn)
    transport, seen = _server()
    async with httpx.AsyncClient(transport=transport) as http:
        await run_reference_load(pool, _client(http, settings), settings, FrozenClock(NOW), D20)
    assert sum(url.endswith("/PR201126.zip") for url in seen) == 1
    app = create_app(settings, clock=FrozenClock(NOW))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        r = (await c.get("/api/data/reference", params={"days": 2})).json()
        assert r["ledger"]["mto"]["ok"] == 3
        assert r["ledger"]["participant_oi"]["rounding_days"] == 3
        assert r["ledger"]["pr_text"]["ok"] == 3
        assert r["ledger"]["market_cap"]["ok"] == 1
        assert r["ledger"]["market_cap"]["missing"] == 1  # report existed only from 2024
        assert r["sources"]["fred_key_configured"] is False
        assert [d["date"] for d in r["participants"]] == ["2026-11-20", "2026-11-19"]
        assert r["participants"][0]["oi"]["FII"]["fut_idx_short"] == 146318
        assert (await c.get("/api/data/reference", params={"days": 0})).status_code == 422

        s = (await c.get("/api/data/stock", params={"symbol": "NSE:SHRIRAMFIN"})).json()
        assert s["delivery"]["days_stored"] == 3
        assert s["delivery"]["avg_bps"] == 7500
        assert s["delivery"]["recent"][0]["date"] == "2026-11-20"
        assert s["market_cap"] == {
            "date": "2026-11-20",
            "series": "EQ",
            "issue_size": "2353045034",
            "market_cap_paisa": "228715977304800",
        }

        bad = await c.post(
            "/api/data/ingest/reference",
            params={"datasets": "nope"},
            headers={"Authorization": f"Bearer {settings.api_auth_token.get_secret_value()}"},
        )
        assert bad.status_code == 422


async def test_deals_are_captured_once_and_an_uncaptured_session_is_a_gap(
    pool: Any, db: Any, test_dsn: str
) -> None:
    await _seed(db)
    settings = _settings(test_dsn)
    deals = ["bulk_deals", "block_deals"]
    transport, _ = _server()
    async with httpx.AsyncClient(transport=transport) as http:
        r1 = await run_reference_load(
            pool, _client(http, settings), settings, FrozenClock(NOW), D20, datasets=deals
        )
        r2 = await run_reference_load(
            pool, _client(http, settings), settings, FrozenClock(NOW), D20, datasets=deals
        )
        skipped = date(2026, 11, 23)
        await db.execute(
            """INSERT INTO app.ohlcv_candles (symbol, timeframe, ts, open, high, low, close, volume)
               VALUES ('NSE:NIFTY 50', '1d', $1, 100, 100, 100, 100, 0)""",
            canonical_daily_ts(skipped, IST),
        )
        # The job missed a trading session, and the bulk file still shows 20 Nov.
        later = date(2026, 11, 24)
        r3 = await run_reference_load(
            pool, _client(http, settings), settings, FrozenClock(NOW), later, datasets=deals
        )
    assert (r1.deals, r1.ok) == (1, 2)
    assert (r2.deals, r2.ok) == (0, 0)  # already captured: nothing twice
    row = await db.fetchrow("SELECT symbol, price_paisa, price_exact FROM app.deals")
    assert (row["symbol"], row["price_paisa"], row["price_exact"]) == (
        "NSE:RELIANCE",
        129016,
        False,
    )
    ledger = {
        (r["dataset"], r["trade_date"]): (r["status"], r["detail"])
        for r in await db.fetch(
            "SELECT dataset, trade_date, status, detail FROM app.reference_files"
        )
    }
    assert ledger[("block_deals", D20)] == (
        "ok",
        "no deals published (empty file; date not stated)",
    )
    # bulk: NSE's file still shows 20 Nov, so 23 Nov was never captured — a gap, for good
    assert ledger[("bulk_deals", later)][0] == "missing"
    # block: an empty file can only mean "none in the latest session"
    assert ledger[("block_deals", later)][0] == "ok"
    assert ledger[("bulk_deals", skipped)][0] == "missing"
    assert ledger[("block_deals", skipped)][0] == "missing"
    assert r3.missing == 3

    app = create_app(settings, clock=FrozenClock(NOW))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        stock = (await c.get("/api/data/stock", params={"symbol": "NSE:RELIANCE"})).json()
        [deal] = stock["events"]["deals"]
        assert deal["date"] == D20.isoformat()
        assert deal["price_paisa"] == 129016
        assert deal["price_exact"] is False


async def test_pr_text_stores_universe_announcements_and_board_meetings(
    pool: Any, db: Any, test_dsn: str
) -> None:
    await _seed(db)
    settings = _settings(test_dsn)
    transport, _ = _server()
    async with httpx.AsyncClient(transport=transport) as http:
        r = await run_reference_load(
            pool, _client(http, settings), settings, FrozenClock(NOW), D20, datasets=["pr_text"]
        )
    assert (r.ok, r.announcements, r.board_meetings) == (3, 3, 3)  # parsed: one per file
    # the same meeting announced in three files is stored once
    assert await db.fetchval("SELECT count(*) FROM app.board_meetings") == 1
    an = await db.fetch(
        "SELECT symbol, announced_on, category, source_symbol FROM app.announcements ORDER BY 2"
    )
    assert next((a["symbol"], a["category"], a["source_symbol"]) for a in an) == (
        "NSE:SHRIRAMFIN",
        "Press Release",
        "SRTRANSFIN",  # the 2022 file used the old name
    )
    bm = await db.fetchrow(
        "SELECT symbol, meeting_date, is_results, first_seen FROM app.board_meetings"
    )
    assert (bm["symbol"], bm["meeting_date"], bm["is_results"], bm["first_seen"]) == (
        "NSE:RELIANCE",
        date(2027, 1, 15),
        True,
        OLD,  # first seen in the oldest file
    )
