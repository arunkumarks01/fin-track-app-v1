"""Slice 1d: an option-chain snapshot end to end against a scripted Kite /quote."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any

import asyncpg
import httpx
import pytest

from app.broker.kite.client import KiteClient
from app.core.tradingdate import MARKET_TZ
from app.data.option_chain import OptionChain
from app.db.migrate import migrate
from tests.conftest import build_settings
from tests.fixtures.clock import FrozenClock
from tests.fixtures.kite_http import KITE_KEY, KITE_SECRET, FakeKiteHTTP, kite_error, kite_ok

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 23, 10, 7, 30, tzinfo=MARKET_TZ)  # Monday, inside the window
EXP1, EXP2, EXP3 = date(2026, 11, 26), date(2026, 12, 3), date(2026, 12, 31)


class Sessions:
    class _S:
        access_token = "tok"

    def current(self) -> Any:
        return self._S()

    async def invalidate(self, reason: str) -> None:
        return None


@pytest.fixture
async def pool(test_dsn: str, db: Any) -> AsyncIterator[Any]:
    await migrate(db)
    p = await asyncpg.create_pool(test_dsn, min_size=1, max_size=3)
    try:
        yield p
    finally:
        await p.close()


async def _instruments(db: Any) -> None:
    rows: list[tuple[Any, ...]] = []
    tok = 1000
    for exp in (EXP1, EXP2, EXP3):
        for strike in range(24_000, 27_001, 100):
            for t in ("CE", "PE"):
                tok += 1
                rows.append(
                    (
                        tok,
                        tok,
                        f"NIFTY{exp:%y%m%d}{strike}{t}",
                        "NIFTY",
                        "NFO",
                        "NFO-OPT",
                        t,
                        exp,
                        strike * 100,
                        5,
                        75,
                    )
                )
    for exp, tsym in ((EXP1, "NIFTY26NOVFUT"), (EXP3, "NIFTY26DECFUT")):
        tok += 1
        rows.append((tok, tok, tsym, "NIFTY", "NFO", "NFO-FUT", "FUT", exp, None, 5, 75))
    await db.executemany(
        """INSERT INTO app.instruments (instrument_token, exchange_token, tradingsymbol, name,
               exchange, segment, instrument_type, expiry, strike, tick_size, lot_size,
               refreshed_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, now())""",
        rows,
    )


def _quote_handler(fake: FakeKiteHTTP, *, drop: str | None = None) -> Any:
    def handler(req: httpx.Request) -> httpx.Response:
        keys = req.url.params.get_list("i")
        assert len(keys) <= 250 or all(k.startswith("NSE:") or "FUT" in k for k in keys)
        data: dict[str, Any] = {}
        for k in keys:
            if k == drop:
                continue
            if k == "NSE:NIFTY 50":
                data[k] = {"last_price": 25512.30}
            elif k.endswith("FUT"):
                data[k] = {"last_price": 25530.00}
            else:
                data[k] = {
                    "last_price": 100.05,
                    "volume": 10,
                    "oi": 500,
                    "last_trade_time": "2026-11-23 10:05:00",
                    "depth": {
                        "buy": [{"price": 100.00, "quantity": 75, "orders": 1}],
                        "sell": [{"price": 100.10, "quantity": 75, "orders": 1}],
                    },
                }
        return kite_ok(data)

    return handler


def _chain(pool: Any, fake: FakeKiteHTTP, **kw: Any) -> OptionChain:
    s = build_settings(
        kite_api_key=KITE_KEY, kite_api_secret=KITE_SECRET, chain_stock_count=0,
        chain_index_underlyings=["NIFTY"], kite_quote_rps=1000.0, **kw,
    )  # fmt: skip
    clock = FrozenClock(NOW)

    async def sleep(_s: float) -> None:
        return None

    kite = KiteClient(httpx.AsyncClient(transport=fake.transport()), s, Sessions(), clock, sleep)
    return OptionChain(pool, kite, s, clock)


async def test_a_snapshot_stores_raw_atm_chains_for_two_expiries(pool: Any, db: Any) -> None:
    await _instruments(db)
    fake = FakeKiteHTTP()
    fake.on("GET", "/quote", _quote_handler(fake))
    r = await _chain(pool, fake).snapshot()

    # spot 25,512.30 -> ATM 25,500; +-15 strikes of 100 = 24,000..27,000 (all 31), CE+PE, 2 expiries
    assert (r.underlyings, r.contracts_requested, r.contracts_stored) == ({"NIFTY": "ok"}, 124, 124)
    assert r.requests == 2  # one for spot + future, one for the 124 option contracts
    ts = datetime(2026, 11, 23, 10, 5, tzinfo=MARKET_TZ)  # 10:07:30 floors to the 5-minute mark
    meta = await db.fetchrow("SELECT * FROM app.option_snapshot_meta")
    assert (meta["snapshot_ts"], meta["spot_paisa"], meta["fut_paisa"], meta["status"]) == (
        ts,
        2551230,
        2553000,
        "ok",
    )
    assert meta["fut_expiry"] == EXP1  # the near-month future, not the December one
    rows = await db.fetch("SELECT * FROM app.option_snapshots")
    assert len(rows) == 124
    assert {r["expiry"] for r in rows} == {EXP1, EXP2}  # EXP3 is out of scope
    one = rows[0]
    assert (one["ltp"], one["volume"], one["oi"], one["bid"], one["bid_qty"], one["ask"]) == (
        10005,
        10,
        500,
        10000,
        75,
        10010,
    )
    assert one["ltt"] == datetime(2026, 11, 23, 4, 35, tzinfo=UTC)

    # the same 5-minute slot twice stores nothing twice
    again = await _chain(pool, fake).snapshot()
    assert (
        again.contracts_stored == 0
        or await db.fetchval("SELECT count(*) FROM app.option_snapshots") == 124
    )


async def test_contracts_kite_has_no_quote_for_make_a_partial_snapshot(pool: Any, db: Any) -> None:
    await _instruments(db)
    missing = f"NFO:NIFTY{EXP1:%y%m%d}25500CE"
    fake = FakeKiteHTTP()
    fake.on("GET", "/quote", _quote_handler(fake, drop=missing))
    r = await _chain(pool, fake).snapshot()
    assert r.underlyings == {"NIFTY": "partial"}
    assert r.contracts_stored == 123
    assert (await db.fetchrow("SELECT status, contracts FROM app.option_snapshot_meta"))[
        "status"
    ] == "partial"


async def test_no_spot_price_is_an_error_row_and_stores_no_contracts(pool: Any, db: Any) -> None:
    await _instruments(db)
    fake = FakeKiteHTTP()
    fake.on("GET", "/quote", _quote_handler(fake, drop="NSE:NIFTY 50"))
    r = await _chain(pool, fake).snapshot()
    assert r.underlyings == {"NIFTY": "error"}
    assert await db.fetchval("SELECT count(*) FROM app.option_snapshots") == 0
    assert (await db.fetchrow("SELECT status, detail FROM app.option_snapshot_meta"))[
        "status"
    ] == "error"


async def test_outside_the_window_or_on_a_kite_failure_nothing_is_stored(
    pool: Any, db: Any
) -> None:
    await _instruments(db)
    fake = FakeKiteHTTP()
    fake.on("GET", "/quote", _quote_handler(fake))
    chain = _chain(pool, fake)
    chain._clock = FrozenClock(datetime(2026, 11, 23, 16, 5, tzinfo=MARKET_TZ))  # after 15:30
    assert (await chain.snapshot()).skipped == "outside the chain window"
    chain._clock = FrozenClock(datetime(2026, 11, 22, 10, 5, tzinfo=MARKET_TZ))  # a Sunday
    assert (await chain.snapshot()).skipped == "outside the chain window"

    failing = FakeKiteHTTP()
    failing.on("GET", "/quote", kite_error(400, "InputException", "bad"))
    r = await _chain(pool, failing).snapshot()
    assert r.stopped_reason is not None
    assert "KiteRequestError" in r.stopped_reason
    assert await db.fetchval("SELECT count(*) FROM app.option_snapshots") == 0


async def test_stock_underlyings_are_ranked_by_option_turnover_from_the_fo_history(
    pool: Any, db: Any
) -> None:
    await db.executemany(
        """INSERT INTO app.instruments (instrument_token, exchange_token, tradingsymbol, name,
               exchange, segment, instrument_type, expiry, strike, tick_size, lot_size,
               refreshed_at)
           VALUES ($1, $1, $2, $3, 'NFO', 'NFO-OPT', 'CE', '2026-12-31', 100000, 5, $4, now())""",
        [(1, "AAA26DEC1000CE", "AAA", 100), (2, "BBB26DEC1000CE", "BBB", 10),
         (3, "CCC26DEC1000CE", "CCC", 1000)],
    )  # fmt: skip
    # turnover = contracts x close x lot: AAA 1000x5.00x100 = 500k, BBB 9000x5.00x10 = 450k,
    # CCC 10x5.00x1000 = 50k
    await db.executemany(
        """INSERT INTO app.fo_daily (trade_date, underlying, kind, expiry, strike_paisa, opt_type,
               open_paisa, high_paisa, low_paisa, close_paisa, settle_paisa, contracts,
               open_interest, oi_change)
           VALUES ('2026-11-20', $1, 'STO', '2026-12-31', 100000, 'CE', 500, 500, 500, 500, 500,
                   $2, 1, 0)""",
        [("AAA", 1000), ("BBB", 9000), ("CCC", 10)],
    )
    s = build_settings(chain_stock_count=2, chain_index_underlyings=[])
    chain = OptionChain(pool, None, s, FrozenClock(NOW))
    async with pool.acquire() as conn:
        assert await chain._rank_stocks(conn, date(2026, 11, 23)) == ["AAA", "BBB"]
        assert await chain._rank_stocks(conn, date(2026, 11, 24)) == [
            "AAA",
            "BBB",
        ]  # cached: a week
    s2 = build_settings(chain_stock_count=2, chain_stock_override=["CCC"])
    override = OptionChain(pool, None, s2, FrozenClock(NOW))
    async with pool.acquire() as conn:
        assert await override._rank_stocks(conn, date(2026, 11, 23)) == ["CCC"]
