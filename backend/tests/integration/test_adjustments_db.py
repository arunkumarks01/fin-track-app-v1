"""Phase 1c on the real DB: NSE archive load, measured adjustment, as-traded views, the audited
provisional repair and the drift re-check."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import AsyncIterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest

from app.broker.kite.client import RateLimiter
from app.core.tradingdate import MARKET_TZ, canonical_daily_ts
from app.data.adjustment import run_adjustment_analysis
from app.data.extract_daily import run_daily_backfill
from app.data.instruments import UniverseMember, sync_universe
from app.data.jobs import IngestRunner
from app.data.nse_archive import NseArchiveClient
from app.data.nse_load import run_nse_archive_load
from app.data.repair import repair_provisional_daily
from app.db.migrate import migrate
from app.main import create_app
from tests.conftest import build_settings
from tests.fixtures.clock import FrozenClock
from tests.fixtures.historical import FakeHistorical

pytestmark = pytest.mark.db

IST = MARKET_TZ
DAYS = [date(2026, 11, d) for d in (9, 10, 11, 12, 13, 16)]  # Mon..Fri, Mon
EX = date(2026, 11, 12)  # AAA splits 1:10 (ex-date)
RAW = {  # NSE official as-traded close, paisa
    date(2026, 11, 9): 100_000,
    date(2026, 11, 10): 101_000,
    date(2026, 11, 11): 102_000,
    date(2026, 11, 12): 10_300,
    date(2026, 11, 13): 10_400,
    date(2026, 11, 16): 10_500,
}
QTY = 50_000
MEMBERS = [
    UniverseMember("NSE:AAA", 101, "equity"),
    UniverseMember("NSE:BBB", 202, "equity"),
    UniverseMember("NSE:NIFTY 50", 256265, "index"),
]
NOW = datetime(2026, 11, 17, 9, 0, tzinfo=IST)  # Tue: Mon 16 is final


CHANGES = (
    "NAME OF THE COMPANY,OLD SYMBOL,NEW SYMBOL,EFFECTIVE DATE\n"
    "Unrelated Ltd,ZZZOLD,ZZZ,01-JAN-2020\n"
)


def _rupees(p: int) -> str:
    return f"{Decimal(p) / 100:.2f}"


def _kite(d: date) -> int:
    return RAW[d] // 10 if d < EX else RAW[d]  # Kite back-adjusts days before the ex-date


def _zip(d: date) -> bytes:
    bc = (
        "SERIES,SYMBOL,SECURITY,RECORD_DT,BC_STRT_DT,BC_END_DT,EX_DT,ND_STRT_DT,ND_END_DT,PURPOSE\n"
        f"EQ,AAA,AAA Ltd,13/11/2026, , ,{EX:%d/%m/%Y}, , ,FVSPLT FRM RS 10 TO RE 1\n"
    )
    p = _rupees(RAW[d])
    pd = (
        "MKT,SERIES,SYMBOL,SECURITY,PREV_CL_PR,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,CLOSE_PRICE,"
        "NET_TRDVAL,NET_TRDQTY,IND_SEC,CORP_IND,TRADES,HI_52_WK,LO_52_WK\n"
        f"N,EQ,AAA,AAA LTD,{p},{p},{p},{p},{p},1.0,{QTY},Y, ,1,{p},{p}\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"Bc{d:%d%m%y}.csv", bc)
        z.writestr(f"Pd{d:%d%m%y}.csv", pd)
    return buf.getvalue()


def _archive(missing: set[date]) -> tuple[httpx.MockTransport, list[str]]:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("symbolchange.csv"):
            return httpx.Response(200, content=CHANGES.encode())
        seen.append(req.url.path)
        d = datetime.strptime(req.url.path.rsplit("/PR", 1)[1][:6], "%d%m%y").date()  # noqa: DTZ007
        if d in missing or d not in RAW:
            return httpx.Response(404)
        return httpx.Response(200, content=_zip(d))

    return httpx.MockTransport(handler), seen


async def _no_sleep(_s: float) -> None:
    return None


def _nse_client(http: httpx.AsyncClient, **kw: Any) -> NseArchiveClient:
    s = build_settings(**kw)
    return NseArchiveClient(http, s, RateLimiter(1000.0, FrozenClock(NOW), _no_sleep), _no_sleep)


async def _bar(db: Any, symbol: str, d: date, price: int, vol: int, ingested: datetime) -> None:
    await db.execute(
        """INSERT INTO app.ohlcv_candles
           (symbol, timeframe, ts, open, high, low, close, volume, oi, ingested_at)
           VALUES ($1, '1d', $2, $3, $3, $3, $3, $4, NULL, $5)""",
        symbol,
        canonical_daily_ts(d, IST),
        price,
        vol,
        ingested,
    )


@pytest.fixture
async def pool(test_dsn: str, db: Any) -> AsyncIterator[Any]:
    await migrate(db)
    await sync_universe(db, MEMBERS, NOW)
    p = await asyncpg.create_pool(test_dsn, min_size=1, max_size=3)
    try:
        yield p
    finally:
        await p.close()


async def _seed_market(db: Any) -> None:
    final = datetime(2026, 11, 17, 9, 0, tzinfo=IST)
    for d in DAYS:
        await _bar(db, "NSE:NIFTY 50", d, 2_500_000, 0, final)
        # Kite's volume is scaled for the split too (x10 before the ex-date)
        await _bar(db, "NSE:AAA", d, _kite(d), QTY * (10 if d < EX else 1), final)


async def test_archive_load_then_measured_adjustment_and_as_traded(
    pool: Any, db: Any, test_dsn: str
) -> None:
    await _seed_market(db)
    transport, seen = _archive(missing={date(2026, 11, 16)})
    # 2-3 measured days per side: allowed here explicitly (the minimum is unit-tested)
    settings = build_settings(
        database_url=test_dsn, nse_archive_start=date(2026, 11, 1), adj_min_window_days=2
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = _nse_client(http)
        report = await run_nse_archive_load(pool, client, settings, FrozenClock(NOW), DAYS[-1])
        assert (report.ok, report.missing, report.errors) == (5, 1, 0)
        assert seen[0].endswith("/PR161126.zip")  # newest first

        ledger = dict(await db.fetch("SELECT trade_date, status FROM app.nse_archive_files"))
        assert ledger[date(2026, 11, 16)] == "missing"
        assert await db.fetchval("SELECT count(*) FROM app.nse_bhav") == 5
        actions = await db.fetch(
            "SELECT symbol, ex_date, kinds, first_seen FROM app.corporate_actions"
        )
        assert [(a["symbol"], a["ex_date"], a["kinds"], a["first_seen"]) for a in actions] == [
            ("NSE:AAA", EX, ["split"], date(2026, 11, 9))  # same action in 5 files → one row
        ]

        # resumable + idempotent: nothing left to do; a missing day is not re-fetched…
        seen.clear()
        again = await run_nse_archive_load(pool, client, settings, FrozenClock(NOW), DAYS[-1])
        assert (again.dates_todo, seen) == (0, [])
        # …unless asked
        retry = await run_nse_archive_load(
            pool, client, settings, FrozenClock(NOW), DAYS[-1], retry_missing=True
        )
        assert (retry.dates_todo, retry.missing) == (1, 1)

    # 1m minutes for AAA on 11-10 (Kite basis x0.1), stored pre-1c: aggregates NULL
    sid = await db.fetchval(
        "INSERT INTO app.instrument_ids (symbol) VALUES ('NSE:AAA') RETURNING sid"
    )
    t0 = datetime(2026, 11, 10, 9, 15, tzinfo=IST)
    t1 = datetime(2026, 11, 10, 9, 16, tzinfo=IST)
    k = _kite(date(2026, 11, 10))
    await db.executemany(
        "INSERT INTO app.ohlcv_1m (ts, volume, open, high, low, close, sid) "
        "VALUES ($1, $2, $3, $3, $3, $3, $4)",
        [(t0, 300_000, k, sid), (t1, 200_000, k, sid)],
    )
    await db.execute(
        """INSERT INTO app.intraday_sessions (sid, session_date, bars, first_ts, last_ts)
           VALUES ($1, '2026-11-10', 2, $2, $3)""",
        sid,
        t0,
        t1,
    )

    adj = await run_adjustment_analysis(pool, settings, FrozenClock(NOW))
    assert adj.events == {"explained": 1}
    days = {
        r["trade_date"]: r
        for r in await db.fetch("SELECT * FROM app.adjustment_days WHERE symbol = 'NSE:AAA'")
    }
    assert float(days[date(2026, 11, 10)]["f_daily"]) == pytest.approx(0.1)
    assert float(days[date(2026, 11, 10)]["v_daily"]) == pytest.approx(10.0)
    assert float(days[date(2026, 11, 13)]["f_daily"]) == pytest.approx(1.0)
    assert days[date(2026, 11, 16)]["quality"] == "unmeasurable"  # its NSE file is missing
    ev = await db.fetchrow("SELECT * FROM app.adjustment_events WHERE symbol = 'NSE:AAA'")
    assert (ev["effective_date"], ev["classification"]) == (EX, "explained")
    idx = await db.fetch(
        "SELECT DISTINCT quality FROM app.adjustment_days WHERE symbol = 'NSE:NIFTY 50'"
    )
    assert [r["quality"] for r in idx] == ["not_applicable"]
    # aggregates were backfilled for the pre-1c session
    assert (
        await db.fetchval("SELECT open_first FROM app.intraday_sessions WHERE sid = $1", sid) == k
    )

    # as-traded 1m = stored / (f x g): back to the official as-traded price, exactly
    rows = await db.fetch(
        "SELECT * FROM app.minute_as_traded('NSE:AAA', $1, $2)",
        t0,
        datetime(2026, 11, 11, tzinfo=IST),
    )
    assert [(r["close"], r["quality"]) for r in rows] == [
        (RAW[date(2026, 11, 10)], "measured"),
        (RAW[date(2026, 11, 10)], "measured"),
    ]
    assert sum(r["volume"] for r in rows) == 50_000  # 500_000 Kite shares / v_daily 10

    # the API serves both views and the summary
    app = create_app(build_settings(database_url=test_dsn), clock=FrozenClock(NOW))
    async with app.router.lifespan_context(app):
        transport2 = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport2, base_url="http://t") as c:
            d1 = (
                await c.get(
                    "/api/data/as-traded",
                    params={
                        "symbol": "NSE:AAA",
                        "grain": "1d",
                        "start": "2026-11-09",
                        "end": "2026-11-16",
                    },
                )
            ).json()
            assert [b["c"] for b in d1["bars"]] == [RAW[d] for d in DAYS[:5]]
            assert {b["quality"] for b in d1["bars"]} == {"official"}
            bad = await c.get(
                "/api/data/as-traded",
                params={
                    "symbol": "NSE:AAA",
                    "grain": "5m",
                    "start": "2026-11-09",
                    "end": "2026-11-09",
                },
            )
            assert bad.status_code == 422
            summary = (await c.get("/api/data/adjustments")).json()
            assert summary["events_by_class"] == {"explained": 1}
            assert summary["nse_archive"]["missing"]["dates"] == 1
            one = (await c.get("/api/data/adjustments", params={"symbol": "NSE:AAA"})).json()
            assert [e["class"] for e in one["events"]] == ["explained"]
            assert (await c.post("/api/data/ingest/nse-archive")).status_code == 401
            assert (await c.post("/api/data/adjustments/analyse")).status_code == 401
            assert (await c.post("/api/data/repair/provisional")).status_code == 401


async def test_archive_refusal_stops_the_run(pool: Any, db: Any, test_dsn: str) -> None:
    await _seed_market(db)
    calls: list[int] = []

    def handler(_req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(403)

    settings = build_settings(database_url=test_dsn, nse_archive_start=date(2026, 11, 1))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await run_nse_archive_load(
            pool, _nse_client(http), settings, FrozenClock(NOW), DAYS[-1]
        )
    assert report.stopped_reason is not None
    assert "refused" in report.stopped_reason
    assert len(calls) == 1  # stopped at once, never retried with other headers
    assert await db.fetchval("SELECT count(*) FROM app.nse_archive_files") == 0


# --- audited repair of provisional daily bars ---------------------------------------------


def _rec(d: date, price: int, vol: int = 1000) -> list[Any]:
    p = Decimal(price) / 100
    return [f"{d:%Y-%m-%d}T00:00:00+0530", p, p, p, p, vol]


async def test_provisional_bars_are_repaired_only_once_final(
    pool: Any, db: Any, test_dsn: str
) -> None:
    fri = date(2026, 11, 13)
    evening = datetime(2026, 11, 13, 18, 20, tzinfo=IST)
    await _bar(db, "NSE:AAA", fri, 10_100, 1000, evening)  # provisional: close will change
    await _bar(db, "NSE:BBB", fri, 20_000, 1000, evening)  # provisional but already right
    src = FakeHistorical({101: [_rec(fri, 10_050)], 202: [_rec(fri, 20_000)]})
    s = build_settings(database_url=test_dsn)

    # Sat 08:00: Friday is not final yet → nothing is touched, nothing is fetched
    early = await repair_provisional_daily(
        pool, src, s, FrozenClock(datetime(2026, 11, 14, 8, 0, tzinfo=IST))
    )
    assert (early.candidates, src.calls) == (0, [])

    later = FrozenClock(datetime(2026, 11, 14, 9, 0, tzinfo=IST))
    report = await repair_provisional_daily(pool, src, s, later)
    assert (report.candidates, report.repaired, report.confirmed) == (2, 1, 1)
    aaa = await db.fetchrow(
        "SELECT close, ingested_at FROM app.ohlcv_candles WHERE symbol='NSE:AAA'"
    )
    assert aaa["close"] == 10_050
    assert aaa["ingested_at"] == later.now()
    q = await db.fetchrow("SELECT reason, raw, source FROM app.ohlcv_quarantine")
    assert (q["reason"], q["source"]) == ("superseded_provisional", "repair")
    assert json.loads(q["raw"])["close"] == 10_100  # the rollback record
    rep = await db.fetchrow("SELECT old_values, new_values, reason FROM app.data_repairs")
    assert (json.loads(rep["old_values"])["close"], json.loads(rep["new_values"])["close"]) == (
        10_100,
        10_050,
    )
    assert await db.fetchval("SELECT count(*) FROM app.data_repairs") == 1  # BBB: confirmed only
    # done: nothing provisional left
    assert (await repair_provisional_daily(pool, src, s, later)).candidates == 0


async def test_unusable_replacement_leaves_the_bar_alone(pool: Any, db: Any, test_dsn: str) -> None:
    fri = date(2026, 11, 13)
    await _bar(db, "NSE:AAA", fri, 10_100, 1000, datetime(2026, 11, 13, 18, 20, tzinfo=IST))
    src = FakeHistorical({101: []})  # Kite returns nothing
    report = await repair_provisional_daily(
        pool,
        src,
        build_settings(database_url=test_dsn),
        FrozenClock(datetime(2026, 11, 14, 9, 0, tzinfo=IST)),
    )
    assert report.repaired == 0
    assert "NSE:AAA" in report.failed
    assert await db.fetchval("SELECT close FROM app.ohlcv_candles") == 10_100
    assert await db.fetchval("SELECT count(*) FROM app.data_repairs") == 0


# --- drift re-check (daily) -----------------------------------------------------------------


async def test_daily_drift_is_recorded_not_overwritten_or_quarantined(
    pool: Any, db: Any, test_dsn: str
) -> None:
    s = build_settings(
        database_url=test_dsn, ingest_daily_start=date(2026, 11, 9), kite_window_days={"day": 30}
    )
    first = [_rec(d, 10_000 + i) for i, d in enumerate(DAYS[:4])]  # up to Thu 11-12
    src = FakeHistorical({101: first})
    await run_daily_backfill(
        pool, src, s, FrozenClock(datetime(2026, 11, 13, 9, 0, tzinfo=IST)), symbols=["NSE:AAA"]
    )
    # Kite then applies a 1:1 bonus (ex 11-13): its history comes back halved
    src.series[101] = [_rec(d, (10_000 + i) // 2) for i, d in enumerate(DAYS[:4])] + [
        _rec(date(2026, 11, 13), 5_100)
    ]
    report = await run_daily_backfill(
        pool, src, s, FrozenClock(datetime(2026, 11, 14, 9, 0, tzinfo=IST)), symbols=["NSE:AAA"]
    )
    assert report.results[0].quarantined == 0
    drift = await db.fetchrow("SELECT ref_date, ratio FROM app.adjustment_drift")
    assert drift["ref_date"] == date(2026, 11, 12)
    assert float(drift["ratio"]) == pytest.approx(0.5, abs=1e-4)
    stored = await db.fetchval(
        "SELECT close FROM app.ohlcv_candles WHERE ts = $1", canonical_daily_ts(DAYS[3], IST)
    )
    assert stored == 10_003  # kept as it was fetched
    assert await db.fetchval(
        "SELECT app.drift_factor('NSE:AAA', '1d', '2026-11-10')"
    ) == pytest.approx(Decimal("0.5"), abs=Decimal("0.0001"))
    assert await db.fetchval("SELECT app.drift_factor('NSE:AAA', '1d', '2026-11-13')") == Decimal(
        1
    )  # bars after the ref date are already in the new basis
    check = await db.fetchrow("SELECT checked_on FROM app.adjustment_checks WHERE symbol='NSE:AAA'")
    assert check["checked_on"] == date(2026, 11, 12)


async def test_non_proportional_change_is_still_a_conflict(
    pool: Any, db: Any, test_dsn: str
) -> None:
    s = build_settings(
        database_url=test_dsn, ingest_daily_start=date(2026, 11, 9), kite_window_days={"day": 30}
    )
    src = FakeHistorical({101: [_rec(d, 10_000) for d in DAYS[:4]]})
    await run_daily_backfill(
        pool, src, s, FrozenClock(datetime(2026, 11, 13, 9, 0, tzinfo=IST)), symbols=["NSE:AAA"]
    )
    revised = _rec(DAYS[3], 10_000)
    revised[2], revised[3] = Decimal("100.50"), Decimal("99.50")  # range moved, close didn't:
    # a valid bar, but not a scaling of history
    src.series[101] = [_rec(d, 10_000) for d in DAYS[:3]] + [revised]
    report = await run_daily_backfill(
        pool, src, s, FrozenClock(datetime(2026, 11, 14, 9, 0, tzinfo=IST)), symbols=["NSE:AAA"]
    )
    assert report.results[0].quarantined == 1
    assert await db.fetchval("SELECT reason FROM app.ohlcv_quarantine") == "conflicts_with_stored"
    assert await db.fetchval("SELECT count(*) FROM app.adjustment_drift") == 0


# --- the daily job chains the archive + analysis -----------------------------------------


async def test_daily_job_loads_archive_and_measures_after_backfill(
    pool: Any, db: Any, test_dsn: str
) -> None:
    s = build_settings(
        database_url=test_dsn,
        ingest_daily_start=date(2026, 11, 9),
        kite_window_days={"day": 30},
        nse_archive_after_daily=True,
        nse_archive_start=date(2026, 11, 1),
        adj_min_window_days=2,
        kite_api_key="k" * 10,
        kite_api_secret="s" * 10,
    )
    src = FakeHistorical(
        {
            101: [_rec(d, _kite(d), QTY * (10 if d < EX else 1)) for d in DAYS],
            256265: [_rec(d, 2_500_000, 0) for d in DAYS],
        }
    )
    transport, _ = _archive(missing=set())
    async with httpx.AsyncClient(transport=transport) as http:
        runner = IngestRunner(pool, src, s, FrozenClock(NOW), _no_sleep, http=http)  # type: ignore[arg-type]
        state = await runner.daily_ingest(["NSE:AAA", "NSE:NIFTY 50"], refresh=False)
    assert state.status == "ok", state.error
    assert state.result["nse"]["ok"] == 6
    assert state.result["adjustments"]["events"] == {"explained": 1}
    assert await db.fetchval("SELECT count(*) FROM app.nse_bhav") == 6


# --- audited archive-day reset: purge + single re-fetch; mislabeled zip → accepted gap -------


async def test_purge_logs_every_row_and_a_mislabeled_refetch_becomes_a_gap(
    pool: Any, db: Any, test_dsn: str
) -> None:
    from app.data.nse_load import reset_archive_day

    await _seed_market(db)
    settings = build_settings(database_url=test_dsn, nse_archive_start=date(2026, 11, 1))
    transport, _ = _archive(missing=set())
    async with httpx.AsyncClient(transport=transport) as http:
        await run_nse_archive_load(pool, _nse_client(http), settings, FrozenClock(NOW), DAYS[-1])
    bad, keep = date(2026, 11, 10), date(2026, 11, 11)
    assert await db.fetchval("SELECT count(*) FROM app.nse_bhav WHERE trade_date = $1", bad) == 1

    async with db.transaction():
        purged = await reset_archive_day(db, bad, True, "zip held another day's files", NOW)
        kept = await reset_archive_day(db, keep, False, "re-load after parser fix", NOW)
    assert (purged, kept) == (1, 0)
    log = await db.fetchrow(
        "SELECT symbol, timeframe, old_values, new_values, reason FROM app.data_repairs"
    )
    assert (log["symbol"], log["timeframe"], log["reason"]) == (
        "NSE:AAA",
        "nse_bhav",
        "zip held another day's files",
    )
    assert json.loads(log["old_values"])["close"] == RAW[bad]
    assert json.loads(log["new_values"]) == {"deleted": True}
    from app.data.adjustment_report import repairs_report

    rep = await repairs_report(db)  # archive purges are not bar repairs
    assert (rep["repairs"], rep["archive_rows_purged"]) == (0, 1)

    # the single re-fetch: the archive still serves another day's files for `bad`
    def mislabeled(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/PR101126.zip"):
            return httpx.Response(200, content=_zip(date(2026, 11, 9)))  # wrong day's members
        return transport.handle_request(req)

    async with httpx.AsyncClient(transport=httpx.MockTransport(mislabeled)) as http:
        report = await run_nse_archive_load(
            pool, _nse_client(http), settings, FrozenClock(NOW), DAYS[-1]
        )
    assert (report.dates_todo, report.ok, report.missing) == (2, 1, 1)
    row = await db.fetchrow(
        "SELECT status, detail FROM app.nse_archive_files WHERE trade_date = $1", bad
    )
    assert row["status"] == "missing"  # accepted gap: never retried again by default
    assert "holds bc file dated 2026-11-09" in row["detail"]
    assert await db.fetchval("SELECT count(*) FROM app.nse_bhav WHERE trade_date = $1", bad) == 0
    assert await db.fetchval("SELECT count(*) FROM app.nse_bhav WHERE trade_date = $1", keep) == 1


# --- per-instrument session calendar on the DB ---------------------------------------------


async def test_session_status_per_instrument_with_auction_step_and_outage(
    pool: Any, db: Any, test_dsn: str
) -> None:
    from datetime import timedelta

    from app.data.intraday_coverage import intraday_report
    from app.data.session_calendar import refresh_session_status

    days: list[date] = []
    d = date(2026, 6, 1)
    while len(days) < 60:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    outage = days[45]
    symbols = [f"NSE:E{i}" for i in range(5)] + ["NSE:NIFTY 50"]
    for sym in symbols:
        sid = await db.fetchval(
            "INSERT INTO app.instrument_ids (symbol) VALUES ($1) RETURNING sid", sym
        )
        rows = []
        for k, day in enumerate(days):
            bars = 375 if sym.endswith("50") or k < 30 else 360  # equities: auction from day 30
            if day == outage:
                bars = 150
            if sym == "NSE:E0" and k == 50:
                bars -= 7  # one real short session
            ts = datetime.combine(day, datetime.min.time(), IST)
            rows.append((sid, day, bars, ts, ts))
        await db.executemany(
            """INSERT INTO app.intraday_sessions (sid, session_date, bars, first_ts, last_ts)
               VALUES ($1, $2, $3, $4, $5)""",
            rows,
        )
        await db.executemany(
            """INSERT INTO app.exchange_sessions
               (exchange, session_date, session_open, expected_minutes, benchmark, refreshed_at)
               VALUES ('NSE', $1, $2, NULL, 'n/a', $2) ON CONFLICT DO NOTHING""",
            [(day, datetime.combine(day, datetime.min.time(), IST)) for day in days],
        )
    s = build_settings(database_url=test_dsn)
    assert await refresh_session_status(db, s, NOW) == 6 * 60
    report = await intraday_report(db, NOW, s.intraday_analysis_start)
    assert report["analysis_start"] == "2016-01-01"  # DEC-2015 default, surfaced
    by = {r["symbol"]: r for r in report["symbols"]}
    assert by["NSE:E0"]["short"] == 1  # the one real short session
    assert by["NSE:E0"]["market_short"] == 1  # the outage: reported, not flagged
    assert by["NSE:E1"]["short"] == 0  # 375 → 360 is a new normal, not 30 short sessions
    assert by["NSE:NIFTY 50"]["complete"] == 59  # the index keeps its own 375 norm
    assert report["summary"]["market_short_sessions"] == 6


# --- renames + trade-for-trade series + parser-version re-scan (Phase 1c close-out) ----------


async def test_rescan_maps_old_names_and_be_series_adding_only_missing_rows(
    pool: Any, db: Any, test_dsn: str
) -> None:
    from app.data.nse_load import PARSE_VERSION

    await _seed_market(db)
    # simulate a day loaded by the v1 parser, when AAA was still "OLDAAA" (renamed 11-12)
    await db.execute(
        """INSERT INTO app.nse_archive_files (trade_date, status, fetched_at, parse_version)
           VALUES ('2026-11-10', 'ok', $1, 1)""",
        NOW,
    )
    await db.execute(
        """INSERT INTO app.nse_bhav (symbol, trade_date, prev_close, open, high, low, close, volume)
           VALUES ('NSE:BBB', '2026-11-10', 5, 5, 5, 5, 5, 1)"""
    )
    header = (
        "MKT,SERIES,SYMBOL,SECURITY,PREV_CL_PR,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,CLOSE_PRICE,"
        "NET_TRDVAL,NET_TRDQTY,IND_SEC,CORP_IND,TRADES,HI_52_WK,LO_52_WK\n"
    )

    def day_zip(d: date) -> bytes:
        rows = (
            "N,EQ,OLDAAA,AAA LTD,1000.00,1000.00,1000.00,1000.00,1000.00,1.0,7,Y, ,1,1,1\n"
            "N,BE,BBB,BBB LTD,9.00,9.00,9.00,9.00,9.00,1.0,3,Y, ,1,1,1\n"
            if d == date(2026, 11, 10)
            else ""
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(
                f"Bc{d:%d%m%y}.csv",
                "SERIES,SYMBOL,SECURITY,RECORD_DT,BC_STRT_DT,BC_END_DT,EX_DT,ND_STRT_DT,ND_END_DT,PURPOSE\n",
            )
            z.writestr(f"Pd{d:%d%m%y}.csv", header + rows)
        return buf.getvalue()

    changes = CHANGES + "AAA Ltd,OLDAAA,AAA,12-NOV-2026\n"

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("symbolchange.csv"):
            return httpx.Response(200, content=changes.encode())
        d = datetime.strptime(req.url.path.rsplit("/PR", 1)[1][:6], "%d%m%y").date()  # noqa: DTZ007
        return httpx.Response(200, content=day_zip(d))

    settings = build_settings(database_url=test_dsn, nse_archive_start=date(2026, 11, 10))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await run_nse_archive_load(
            pool, _nse_client(http), settings, FrozenClock(NOW), date(2026, 11, 10)
        )
    assert report.dates_todo == 1  # the v1 day is re-scanned once
    assert report.symbol_changes == 2
    aaa = await db.fetchrow(
        "SELECT close, series, source_symbol FROM app.nse_bhav WHERE symbol='NSE:AAA'"
    )
    assert (aaa["close"], aaa["series"], aaa["source_symbol"]) == (100_000, "EQ", "OLDAAA")
    # BBB already had a row for that day: never changed by the re-scan (BE row not forced in)
    assert await db.fetchval("SELECT close FROM app.nse_bhav WHERE symbol='NSE:BBB'") == 5
    assert (
        await db.fetchval(
            "SELECT parse_version FROM app.nse_archive_files WHERE trade_date='2026-11-10'"
        )
        == PARSE_VERSION
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        again = await run_nse_archive_load(
            pool, _nse_client(http), settings, FrozenClock(NOW), date(2026, 11, 10)
        )
    assert again.dates_todo == 0


async def test_no_symbol_change_list_means_no_load(pool: Any, db: Any, test_dsn: str) -> None:
    await _seed_market(db)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("symbolchange.csv"):
            return httpx.Response(503)
        return httpx.Response(404)

    settings = build_settings(
        database_url=test_dsn, nse_archive_start=date(2026, 11, 1), nse_archive_retries=0
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await run_nse_archive_load(
            pool, _nse_client(http), settings, FrozenClock(NOW), DAYS[-1]
        )
    assert report.stopped_reason is not None
    assert "symbol-change" in report.stopped_reason
    assert await db.fetchval("SELECT count(*) FROM app.nse_archive_files") == 0
    # with a stored list, a failed refresh falls back to it and the load proceeds
    await db.execute(
        "INSERT INTO app.symbol_changes VALUES ('ZZZOLD', 'ZZZ', '2020-01-01', 'Unrelated Ltd')"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        ok = await run_nse_archive_load(
            pool, _nse_client(http), settings, FrozenClock(NOW), DAYS[-1]
        )
    assert ok.stopped_reason is None
    assert ok.missing == 6


async def test_analysis_reparses_stored_purposes_with_the_current_parser(
    pool: Any, db: Any, test_dsn: str
) -> None:
    # a row stored by an older parser that missed "TORS5" (TECHM 2015): only the bonus parsed
    await db.execute(
        """INSERT INTO app.corporate_actions
           (symbol, series, ex_date, purpose, kinds, bonus_new, bonus_held, first_seen)
           VALUES ('NSE:AAA', 'EQ', '2026-11-12', 'BONUS1:1/FV SPL-RS10TORS5', '{bonus}', 1, 1,
                   '2026-11-01')"""
    )
    await run_adjustment_analysis(pool, build_settings(database_url=test_dsn), FrozenClock(NOW))
    r = await db.fetchrow(
        "SELECT kinds, split_from_paisa, split_to_paisa FROM app.corporate_actions"
    )
    assert (r["kinds"], r["split_from_paisa"], r["split_to_paisa"]) == (
        ["split", "bonus"],
        1000,
        500,
    )


async def test_review_lists_material_steps_and_counts_refit_noise(db: Any) -> None:
    """Sub-threshold unexplained steps are refit noise: counted, not listed. Suspects, official
    mismatches and any step whose size is unknown are always listed (fail closed)."""
    from app.data.adjustment_report import adjustments_summary

    await migrate(db)
    rows = [
        ("NSE:A", date(2026, 1, 5), "unexplained_small", 0.0001),  # noise
        ("NSE:A", date(2026, 2, 5), "unexplained_small", 0.0003),  # noise
        ("NSE:B", date(2026, 3, 5), "unexplained_small", 0.012),  # material
        ("NSE:C", date(2026, 4, 5), "unexplained_small", None),  # size unknown → listed
        ("NSE:F", date(2026, 6, 5), "unexplained_small", Decimal("0.0025")),  # exact, as stored
        ("NSE:D", date(2025, 1, 10), "suspect", 0.75),
        ("NSE:E", date(2026, 5, 5), "explained", None),
        ("NSE:G", date(2023, 3, 2), "corroborated", None),  # DEC-CORROB: always listed
    ]
    await db.executemany(
        """INSERT INTO app.adjustment_events
           (symbol, effective_date, ratio, g_ratio, action_ids, classification, detail, magnitude)
           VALUES ($1, $2, 1, NULL, '{}', $3, 'x', $4)""",
        rows,
    )
    s = await adjustments_summary(db, build_settings(adj_review_min_step=0.0025), NOW)
    assert [(r["symbol"], r["class"]) for r in s["review"]] == [
        ("NSE:D", "suspect"),
        ("NSE:G", "corroborated"),
        ("NSE:B", "unexplained_small"),
        ("NSE:F", "unexplained_small"),
        ("NSE:C", "unexplained_small"),
    ]
    assert s["review"][2]["magnitude"] == pytest.approx(0.012)
    assert s["review_noise"] == {"count": 2, "below": 0.0025}
    with pytest.raises(asyncpg.CheckViolationError):  # the class list is closed (0009)
        await db.execute(
            """INSERT INTO app.adjustment_events
               (symbol, effective_date, ratio, action_ids, classification, detail)
               VALUES ('NSE:H', '2026-01-01', 1, '{}', 'probably_fine', 'x')"""
        )
