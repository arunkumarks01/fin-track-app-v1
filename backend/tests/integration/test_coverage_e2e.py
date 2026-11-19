"""Phase-1 gate: the coverage report, and the whole 1a pipeline end-to-end.

The e2e test drives the real app + real KiteClient over MockTransport:
Kite session → POST /api/data/ingest/daily → instruments CSV → universe → historical day
candles → validation → DB → GET /api/data/coverage.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ, canonical_daily_ts
from app.data.coverage import coverage_report, format_report
from app.data.instruments import UniverseMember, sync_universe
from app.db.migrate import migrate
from app.main import create_app
from tests.conftest import TEST_TOKEN, build_settings
from tests.fixtures.candles import Pattern, make_daily_candles
from tests.fixtures.clock import FrozenClock
from tests.fixtures.historical import kite_record, record_ts
from tests.fixtures.instruments import instruments_csv
from tests.fixtures.kite_http import KITE_KEY, KITE_SECRET, FakeKiteHTTP

pytestmark = pytest.mark.db

IST = MARKET_TZ
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
START = date(2015, 1, 1)
N_BARS = 400  # 400 weekdays from 2015-01-01 -> 2016-07-14
LAST = make_daily_candles(Pattern.TREND, n=N_BARS, start=START)[-1].ts.date()
NOW = datetime.combine(LAST, datetime.min.time(), IST).replace(hour=18)


def gate_settings(test_dsn: str, **kw: Any) -> Settings:
    base: dict[str, Any] = {
        "ingest_daily_start": START,
        "kite_historical_rps": 1000.0,
        "kite_default_rps": 1000.0,
    }
    base.update(kw)
    return build_settings(
        database_url=test_dsn, kite_api_key=KITE_KEY, kite_api_secret=KITE_SECRET, **base
    )


# --- coverage report thresholds on hand-built data ---
async def _seed(db: Any, members: list[UniverseMember], bars: dict[str, tuple[date, int]]) -> None:
    await migrate(db)
    await sync_universe(db, members, NOW)
    for symbol, (start, n) in bars.items():
        candles = make_daily_candles(Pattern.TREND, n=n, start=start, symbol=symbol)
        await db.executemany(
            """INSERT INTO app.ohlcv_candles (symbol, timeframe, ts, open, high, low, close, volume)
               VALUES ($1, '1d', $2, $3, $4, $5, $6, $7)""",
            [(c.symbol, c.ts, c.open, c.high, c.low, c.close, c.volume) for c in candles],
        )


EQ = [UniverseMember(f"NSE:S{i:02d}", 1000 + i, "equity") for i in range(20)]
IDX = [UniverseMember("NSE:NIFTY 50", 1, "index")]


async def test_gate_passes_at_threshold(db: Any, test_dsn: str) -> None:
    bars = {m.symbol: (date(2015, 6, 1), 300) for m in EQ}
    bars["NSE:S00"] = (date(2015, 6, 1), 249)  # 1 of 21 short -> 95.2% >= 95%
    bars["NSE:NIFTY 50"] = (START, N_BARS)
    await _seed(db, EQ + IDX, bars)
    report = await coverage_report(db, gate_settings(test_dsn), NOW)
    assert report.passed, format_report(report)
    assert report.to_dict()["summary"]["sufficient"] == 20


async def test_gate_fails_below_symbol_pct(db: Any, test_dsn: str) -> None:
    bars = {m.symbol: (date(2015, 6, 1), 300) for m in EQ}
    bars["NSE:S00"] = bars["NSE:S01"] = (date(2015, 6, 1), 249)  # 19/21 = 90.5%
    bars["NSE:NIFTY 50"] = (START, N_BARS)
    await _seed(db, EQ + IDX, bars)
    report = await coverage_report(db, gate_settings(test_dsn), NOW)
    assert not report.passed
    assert report.checks["sufficient_symbols"] is False
    assert "90.5%" in report.failures[0]


async def test_gate_fails_on_shallow_index(db: Any, test_dsn: str) -> None:
    bars = {m.symbol: (date(2015, 6, 1), 300) for m in EQ}
    bars["NSE:NIFTY 50"] = (date(2015, 3, 2), 300)  # starts after 2015-01-31
    await _seed(db, EQ + IDX, bars)
    report = await coverage_report(db, gate_settings(test_dsn), NOW)
    assert report.checks["indices_deep"] is False
    assert any("NIFTY 50" in f for f in report.failures)


async def test_gate_fails_closed_on_empty_universe(db: Any, test_dsn: str) -> None:
    await migrate(db)
    report = await coverage_report(db, gate_settings(test_dsn), NOW)
    assert not report.passed
    assert report.failures == ["universe is empty"]


async def test_staleness_reported_not_hidden(db: Any, test_dsn: str) -> None:
    await _seed(db, IDX, {"NSE:NIFTY 50": (START, N_BARS - 10)})  # ends 10 sessions early
    report = await coverage_report(db, gate_settings(test_dsn), NOW)
    s = report.symbols[0]
    assert s.stale_weekdays == 10
    assert s.fresh is False
    assert s.scorable is False


# --- the whole 1a pipeline end-to-end ---
def _historical_handler(request: httpx.Request) -> httpx.Response:
    token = int(request.url.path.split("/")[3])
    start = datetime.fromisoformat(request.url.params["from"]).replace(tzinfo=IST)
    end = datetime.fromisoformat(request.url.params["to"]).replace(tzinfo=IST)
    candles = make_daily_candles(Pattern.TREND, n=N_BARS, start=START, seed=token % 997)
    rows = [kite_record(c) for c in candles]
    return httpx.Response(
        200,
        content=(
            '{"status":"success","data":{"candles":['
            + ",".join(
                f'["{r[0]}",{r[1]},{r[2]},{r[3]},{r[4]},{r[5]}]'
                for r in rows
                if start <= record_ts(r) <= end
            )
            + "]}}"
        ),
        headers={"content-type": "application/json"},
    )


class _Router(FakeKiteHTTP):
    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/instruments/historical/"):
            self.requests.append(request)
            return _historical_handler(request)
        return super().handler(request)


async def test_end_to_end_ingest_reaches_green_gate(db: Any, test_dsn: str) -> None:
    await migrate(db)
    await db.execute(
        """INSERT INTO app.kite_session (id, user_id, access_token, login_time, expires_at)
           VALUES (1, 'AB1234', 'acc_tok', $1, $2)""",
        NOW - timedelta(hours=2),
        NOW + timedelta(hours=10),
    )
    fake = _Router()
    fake.on("GET", "/instruments", httpx.Response(200, text=instruments_csv()))
    app = create_app(
        gate_settings(test_dsn), clock=FrozenClock(NOW), http_transport=fake.transport()
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            before = (await c.get("/api/data/coverage")).json()
            assert before["passed"] is False
            assert (await c.post("/api/data/ingest/daily")).status_code == 401

            r = await c.post("/api/data/ingest/daily", headers=AUTH)
            assert r.status_code == 202
            busy = await c.post("/api/data/ingest/daily", headers=AUTH)
            assert busy.status_code == 409
            await app.state.ingest.wait()

            job = (await c.get("/api/data/jobs")).json()["last"]
            assert job["status"] == "ok", job
            assert job["result"]["universe"]["active"] == 9
            assert job["result"]["backfill"]["inserted"] == 9 * N_BARS

            cov = (await c.get("/api/data/coverage")).json()
            assert cov["passed"] is True, cov["failures"]
            assert cov["summary"] == {
                "symbols": 9,
                "sufficient": 9,
                "sufficient_pct": 100.0,
                "scorable": 9,
                "stale": 0,
                "clean_bars": 9 * N_BARS,
                "quarantined": 0,
                "quarantined_total": 0,
                "insufficient": [],
                "duplicate_dates": 0,
            }
    # every request carried the Kite auth header; no request ever left the MockTransport
    assert all(req.headers["Authorization"] == f"token {KITE_KEY}:acc_tok" for req in fake.requests)
    newest = await db.fetchval("SELECT max(ts) FROM app.ohlcv_candles WHERE timeframe = '1d'")
    assert newest == canonical_daily_ts(LAST, IST)


async def test_ingest_without_session_refused(db: Any, test_dsn: str) -> None:
    app = create_app(
        gate_settings(test_dsn), clock=FrozenClock(NOW), http_transport=FakeKiteHTTP().transport()
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post("/api/data/ingest/daily", headers=AUTH)
    assert r.status_code == 409
    assert "log in" in r.json()["detail"]


async def test_holey_symbol_is_not_sufficient(db: Any, test_dsn: str) -> None:
    # 400 bars spread over ~2x the weekdays: deep and "enough bars", but full of holes
    await migrate(db)
    await sync_universe(db, IDX + EQ[:1], NOW)
    good = make_daily_candles(Pattern.TREND, n=N_BARS, start=START, symbol="NSE:NIFTY 50")
    holey = make_daily_candles(Pattern.TREND, n=2 * N_BARS, start=START, symbol="NSE:S00")[::2]
    await db.executemany(
        """INSERT INTO app.ohlcv_candles (symbol, timeframe, ts, open, high, low, close, volume)
           VALUES ($1, '1d', $2, $3, $4, $5, $6, $7)""",
        [(c.symbol, c.ts, c.open, c.high, c.low, c.close, c.volume) for c in good + holey],
    )
    report = await coverage_report(db, gate_settings(test_dsn), NOW)
    s00 = next(s for s in report.symbols if s.symbol == "NSE:S00")
    assert s00.clean_bars == N_BARS
    assert s00.sufficient is False
    assert s00.insufficient_reason is not None
    assert "weekdays missing" in s00.insufficient_reason
    assert report.checks["sufficient_symbols"] is False  # 1 of 2 = 50%


async def test_quarantine_counted_only_while_unresolved(db: Any, test_dsn: str) -> None:
    await _seed(db, IDX, {"NSE:NIFTY 50": (START, N_BARS)})
    missing_day = datetime(2016, 8, 1, 9, 15, tzinfo=IST)  # after the seeded range
    stored_day = datetime(2015, 1, 1, 9, 15, tzinfo=IST)  # this date has a clean bar
    await db.executemany(
        """INSERT INTO app.ohlcv_quarantine
           (symbol, timeframe, ts, reason, detail, raw, source, quarantined_at)
           VALUES ('NSE:NIFTY 50', '1d', $1, 'non_canonical_ts', 'old rule', '[]', 'test', $2)""",
        [(missing_day, NOW), (stored_day, NOW)],
    )
    s = (await coverage_report(db, gate_settings(test_dsn), NOW)).symbols[0]
    assert (s.quarantined, s.quarantined_total) == (1, 2)
