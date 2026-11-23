"""Slice 1f: F&O history loaded from a fake NSE archive."""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Any

import asyncpg
import httpx
import pytest

from app.broker.kite.client import RateLimiter
from app.core.tradingdate import MARKET_TZ, canonical_daily_ts
from app.data.fo_load import run_fo_load
from app.data.instruments import UniverseMember, sync_universe
from app.data.nse_archive import NseArchiveClient
from app.db.migrate import migrate
from app.main import create_app
from tests.conftest import build_settings
from tests.fixtures.clock import FrozenClock

pytestmark = pytest.mark.db

IST = MARKET_TZ
NOW = datetime(2026, 11, 21, 9, 0, tzinfo=IST)
OLD_DAY = date(2023, 6, 15)  # old format
NEW_DAY = date(2026, 11, 20)  # UDiFF
GAP_DAY = date(2026, 11, 19)  # the archive has no file
DAYS = [OLD_DAY, GAP_DAY, NEW_DAY]

OLD_HEAD = (
    "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,"
    "VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,\n"
)
NEW_HEAD = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,"
    "StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,"
    "SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty\n"
)


def old_zip(d: date, stated: date | None = None) -> bytes:
    s = stated or d
    body = OLD_HEAD + (
        f"FUTIDX,NIFTY,29-Jun-2023,0,XX,18700,18800,18650,18760,18760,100,1,5000,10,{s:%d-%b-%Y}\n"
        f"OPTIDX,NIFTY,29-Jun-2023,18700,CE,100.5,120,90,110,110,50,1,900,5,{s:%d-%b-%Y}\n"
    ).replace(f"{s:%d-%b-%Y}", f"{s:%d-%b-%Y}".upper())
    return _zip(f"fo{d:%d%b%Y}bhav.csv", body.replace(",\n", "\n").replace("\n", ",\n"))


def new_zip(d: date, stated: date | None = None) -> bytes:
    s = stated or d
    row = (
        f"{s:%Y-%m-%d},{s:%Y-%m-%d},FO,NSE,IDF,1,,NIFTY,,2026-12-29,2026-12-29,,,NIFTY26DECFUT,"
        "25800,25900,25700,25850,25850,25800,25814,25850,100000,2000,5000,1000,40,F1,75\n"
    )
    return _zip("BhavCopy.csv", NEW_HEAD + row)


def _zip(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def server(**by_day: httpx.Response) -> tuple[httpx.MockTransport, list[str]]:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        seen.append(url)
        for key, resp in by_day.items():
            if key in url:
                return resp
        if "BhavCopy_NSE_FO" in url:
            d = datetime.strptime(url.rsplit("_F_", 1)[0][-8:], "%Y%m%d").date()  # noqa: DTZ007
            return httpx.Response(404) if d == GAP_DAY else httpx.Response(200, content=new_zip(d))
        return httpx.Response(200, content=old_zip(OLD_DAY))

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


def _client(http: httpx.AsyncClient, s: Any) -> NseArchiveClient:
    return NseArchiveClient(http, s, RateLimiter(1000.0, FrozenClock(NOW), _no_sleep), _no_sleep)


async def _seed(db: Any) -> None:
    await migrate(db)
    await sync_universe(db, [UniverseMember("NSE:NIFTY 50", 3, "index")], NOW)
    for d in DAYS:
        await db.execute(
            """INSERT INTO app.ohlcv_candles (symbol, timeframe, ts, open, high, low, close, volume)
               VALUES ('NSE:NIFTY 50', '1d', $1, 100, 100, 100, 100, 0)""",
            canonical_daily_ts(d, IST),
        )


def _settings(dsn: str, **kw: Any) -> Any:
    return build_settings(database_url=dsn, nse_archive_start=date(2023, 1, 1), **kw)


async def test_loads_both_formats_newest_first_with_a_gap_and_resumes(
    pool: Any, db: Any, test_dsn: str
) -> None:
    await _seed(db)
    s = _settings(test_dsn)
    transport, seen = server()
    async with httpx.AsyncClient(transport=transport) as http:
        r = await run_fo_load(pool, _client(http, s), s, FrozenClock(NOW), NEW_DAY)
        assert (r.todo, r.ok, r.missing, r.errors, r.rows) == (3, 2, 1, 0, 3)
        assert "20261120" in seen[0]  # newest first, UDiFF
        assert seen[-1].endswith("/2023/JUN/fo15JUN2023bhav.csv.zip")  # old format
        rows = await db.fetch(
            "SELECT trade_date, kind, opt_type, strike_paisa, close_paisa, lot_size, "
            "underlying_paisa FROM app.fo_daily ORDER BY 1, 3"
        )
        assert [(x["trade_date"], x["kind"], x["opt_type"], x["lot_size"]) for x in rows] == [
            (OLD_DAY, "IDO", "CE", None),
            (OLD_DAY, "IDF", "FUT", None),
            (NEW_DAY, "IDF", "FUT", 75),
        ]
        assert rows[2]["underlying_paisa"] == 2581400
        led = {
            x["trade_date"]: x["status"]
            for x in await db.fetch(
                "SELECT trade_date, status FROM app.reference_files WHERE dataset = 'fo_bhav'"
            )
        }
        assert led == {OLD_DAY: "ok", GAP_DAY: "missing", NEW_DAY: "ok"}
        seen.clear()
        again = await run_fo_load(pool, _client(http, s), s, FrozenClock(NOW), NEW_DAY)
        assert (again.todo, seen) == (0, [])  # resumable; the gap waits for retry_missing


async def test_a_mislabelled_file_is_a_gap_and_a_403_stops_the_run(
    pool: Any, db: Any, test_dsn: str
) -> None:
    await _seed(db)
    s = _settings(test_dsn)
    wrong = new_zip(NEW_DAY, stated=date(2026, 11, 18))
    transport, _ = server(**{"20261120": httpx.Response(200, content=wrong)})
    async with httpx.AsyncClient(transport=transport) as http:
        r = await run_fo_load(pool, _client(http, s), s, FrozenClock(NOW), NEW_DAY)
    assert r.missing == 2  # the mislabelled day and the 404 day
    assert (
        await db.fetchval("SELECT count(*) FROM app.fo_daily WHERE trade_date = $1", NEW_DAY) == 0
    )

    refuse = httpx.MockTransport(lambda _r: httpx.Response(403))
    async with httpx.AsyncClient(transport=refuse) as http:
        r2 = await run_fo_load(
            pool, _client(http, s), s, FrozenClock(NOW), NEW_DAY, retry_missing=True
        )
    assert r2.stopped_reason is not None
    assert "refused" in r2.stopped_reason
    assert r2.done == 0


async def test_the_load_stops_at_the_database_size_budget(
    pool: Any, db: Any, test_dsn: str
) -> None:
    await _seed(db)
    s = _settings(test_dsn, fo_db_budget_gb=0.0001)  # far below the real size
    transport, seen = server()
    async with httpx.AsyncClient(transport=transport) as http:
        r = await run_fo_load(pool, _client(http, s), s, FrozenClock(NOW), NEW_DAY)
    assert r.stopped_reason is not None
    assert "FO_DB_BUDGET_GB" in r.stopped_reason
    assert (r.done, seen) == (0, [])  # nothing fetched once over budget


async def test_fo_summary_endpoint(pool: Any, db: Any, test_dsn: str) -> None:
    await _seed(db)
    s = _settings(test_dsn)
    transport, _ = server()
    async with httpx.AsyncClient(transport=transport) as http:
        await run_fo_load(pool, _client(http, s), s, FrozenClock(NOW), NEW_DAY)
    app = create_app(s, clock=FrozenClock(NOW))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        j = (await c.get("/api/data/fo")).json()
    assert (j["days_ok"], j["days_missing"], j["rows"], j["underlyings"]) == (2, 1, 3, 1)
    assert (j["oldest"], j["newest"]) == ("2023-06-15", "2026-11-20")
    assert j["rows_by_kind"] == {"IDF": 2, "IDO": 1}
