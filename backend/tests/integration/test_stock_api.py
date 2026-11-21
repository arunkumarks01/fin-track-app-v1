"""Stock page API (read-only): one stock's names, NSE actions vs Kite, data health."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.core.tradingdate import MARKET_TZ, canonical_daily_ts
from app.data.adjustment import load_actions
from app.data.instruments import UniverseMember, sync_universe
from app.db.migrate import migrate
from app.main import create_app
from tests.conftest import build_settings
from tests.fixtures.clock import FrozenClock

pytestmark = pytest.mark.db

IST = MARKET_TZ
NOW = datetime(2026, 11, 20, 9, 0, tzinfo=IST)
DAYS = [date(2026, 11, d) for d in (9, 10, 11, 12, 13, 16, 17, 18, 19)]


async def _action(db: Any, series: str, ex: date, purpose: str, kinds: list[str], **kw: Any) -> int:
    return int(
        await db.fetchval(
            """INSERT INTO app.corporate_actions
               (symbol, series, ex_date, record_date, purpose, kinds, bonus_new, bonus_held,
                dividend_paisa, first_seen)
               VALUES ('NSE:AAA', $1, $2, $2, $3, $4, $5, $6, $7, $2) RETURNING id""",
            series,
            ex,
            purpose,
            kinds,
            kw.get("bonus_new"),
            kw.get("bonus_held"),
            kw.get("dividend_paisa"),
        )
    )


async def _seed(db: Any) -> None:
    await migrate(db)
    await sync_universe(
        db,
        [UniverseMember("NSE:AAA", 101, "equity"), UniverseMember("NSE:NIFTY 50", 256265, "index")],
        NOW,
    )
    await db.executemany(
        """INSERT INTO app.instruments (instrument_token, exchange_token, tradingsymbol, name,
               exchange, segment, instrument_type, expiry, strike, tick_size, lot_size,
               refreshed_at)
           VALUES ($1, $1, $2, $3, $4, $5, $6, $7, NULL, $8, $9, $10)""",
        [
            (101, "AAA", "AAA LTD", "NSE", "NSE", "EQ", None, 5, 1, NOW),
            (256265, "NIFTY 50", "NIFTY 50", "NSE", "INDICES", "EQ", None, 0, 0, NOW),
            (9001, "AAA26OCTFUT", "AAA", "NFO", "NFO-FUT", "FUT", date(2026, 10, 29), 5, 400, NOW),
            (9002, "AAA26NOVFUT", "AAA", "NFO", "NFO-FUT", "FUT", date(2026, 11, 26), 5, 500, NOW),
            (9003, "AAA26DECFUT", "AAA", "NFO", "NFO-FUT", "FUT", date(2026, 12, 31), 5, 500, NOW),
        ],
    )
    # AAA was OLDAAA, which was OLDEST; a bad row AAA -> OLDEST would loop forever unguarded
    await db.executemany(
        """INSERT INTO app.symbol_changes (old_symbol, new_symbol, effective_date, company)
           VALUES ($1, $2, $3, 'Aaa Limited')""",
        [
            ("OLDAAA", "AAA", date(2024, 5, 1)),
            ("OLDEST", "OLDAAA", date(2020, 1, 1)),
            ("AAA", "OLDEST", date(2019, 1, 1)),
        ],
    )
    for i, d in enumerate(DAYS):
        k = 10_000 + i * 100
        await db.execute(
            """INSERT INTO app.ohlcv_candles (symbol, timeframe, ts, open, high, low, close, volume)
               VALUES ('NSE:AAA', '1d', $1, $2, $3, $4, $5, 1000)""",
            canonical_daily_ts(d, IST),
            k,
            k + 50,
            k - 50,
            k + 10,
        )
    sid = await db.fetchval(
        "INSERT INTO app.instrument_ids (symbol) VALUES ('NSE:AAA') RETURNING sid"
    )
    for d in DAYS[-2:]:
        open_ = datetime.combine(d, datetime.min.time(), IST).replace(hour=9, minute=15)
        await db.execute(
            """INSERT INTO app.exchange_sessions
               (exchange, session_date, session_open, expected_minutes, benchmark, refreshed_at)
               VALUES ('NSE', $1, $2, NULL, 'n/a', $2)""",
            d,
            open_,
        )
        await db.execute(
            """INSERT INTO app.intraday_sessions (sid, session_date, bars, first_ts, last_ts)
               VALUES ($1, $2, 30, $3, $4)""",
            sid,
            d,
            open_,
            open_ + timedelta(minutes=29),
        )
    # measured windows: 0 = days 0..5, 1 = days 6..8 (a bonus at day 6)
    await db.executemany(
        """INSERT INTO app.adjustment_days
           (symbol, trade_date, window_no, f_daily, b_daily, g_minute, v_daily, v_minute, quality)
           VALUES ('NSE:AAA', $1, $2, 1, 0, 1, 1, 1, 'measured')""",
        [(d, 0 if i < 6 else 1) for i, d in enumerate(DAYS)],
    )
    bonus_eq = await _action(db, "EQ", DAYS[6], "BONUS 1:1", ["bonus"], bonus_new=1, bonus_held=1)
    await _action(db, "BE", DAYS[6], "BONUS 1:1", ["bonus"], bonus_new=1, bonus_held=1)
    await _action(db, "EQ", DAYS[3], "DIV - RS 5 PER SH", ["dividend"], dividend_paisa=500)
    await _action(db, "EQ", date(2026, 10, 1), "DIV - RS 4 PER SH", ["dividend"])
    await _action(db, "N5", DAYS[2], "INTEREST PAYMENT", ["other"])  # a bond: not the share
    await db.executemany(
        """INSERT INTO app.adjustment_events
           (symbol, effective_date, ratio, action_ids, classification, detail, magnitude)
           VALUES ('NSE:AAA', $1, $2, $3, $4, $5, $6)""",
        [
            (DAYS[6], 0.5, [bonus_eq], "explained", "Kite applied bonus", None),
            (DAYS[1], 0.999, [], "unexplained_small", "refit", 0.001),
            (DAYS[4], 0.95, [], "suspect", "step not explained", 0.05),
        ],
    )
    await db.execute(
        """INSERT INTO app.ohlcv_quarantine (symbol, timeframe, ts, reason, detail, raw, source,
               quarantined_at)
           VALUES ('NSE:AAA', '1d', $1, 'non_canonical_ts', 'x', '{}', 'kite', $1)""",
        canonical_daily_ts(DAYS[0], IST),
    )
    await db.execute(
        """INSERT INTO app.ohlcv_flags (symbol, timeframe, ts, flag, detail, flagged_at)
           VALUES ('NSE:AAA', '1d', $1, 'large_gap', 'x', $1)""",
        canonical_daily_ts(DAYS[2], IST),
    )


@pytest.fixture
async def client(db: Any, test_dsn: str) -> Any:
    await _seed(db)
    app = create_app(build_settings(database_url=test_dsn), clock=FrozenClock(NOW))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield c


async def _get(client: Any, symbol: str) -> Any:
    return await client.get("/api/data/stock", params={"symbol": symbol})


async def test_stock_basics_names_and_nearest_live_future(client: Any) -> None:
    p = (await _get(client, "NSE:AAA")).json()
    assert (p["name"], p["kind"], p["tick_size_paisa"]) == ("AAA LTD", "equity", 5)
    assert p["futures"] == {"lot_size": 500, "expiry": "2026-11-26"}  # expired Oct skipped
    assert [(n["old"], n["new"], n["date"]) for n in p["names"]] == [
        ("OLDAAA", "AAA", "2024-05-01"),
        ("OLDEST", "OLDAAA", "2020-01-01"),
    ]  # the looping row back to AAA is not followed


async def test_actions_are_share_series_only_each_with_kite_status(client: Any) -> None:
    p = (await _get(client, "NSE:AAA")).json()
    got = [(a["ex_date"], a["purpose"], a["series"], a["kite"]) for a in p["actions"]]
    assert got == [
        ("2026-11-17", "BONUS 1:1", ["BE", "EQ"], "matched"),  # one row across series
        ("2026-11-12", "DIV - RS 5 PER SH", ["EQ"], "no_change"),  # measured, same window
        ("2026-10-01", "DIV - RS 4 PER SH", ["EQ"], "not_measured"),  # before our data
    ]
    assert p["actions"][0]["detail"] == "Kite applied bonus"
    assert p["actions"][1]["dividend_paisa"] == 500
    assert [a["kinds"] for a in p["actions"]] == [["bonus"], ["dividend"], ["dividend"]]
    assert p["other_security_actions"] == 1  # the bond's interest payment, counted apart


async def test_unlisted_steps_newest_first_small_ones_marked(client: Any) -> None:
    p = (await _get(client, "NSE:AAA")).json()
    assert [(s["date"], s["class"], s["small"]) for s in p["unlisted_steps"]] == [
        ("2026-11-13", "suspect", False),
        ("2026-11-10", "unexplained_small", True),  # below ADJ_REVIEW_MIN_STEP
    ]


async def test_data_health_matches_the_gate_numbers(client: Any) -> None:
    p = (await _get(client, "NSE:AAA")).json()
    d = p["daily"]
    assert d["coverage"]["clean_bars"] == len(DAYS)
    assert d["coverage"]["newest"] == "2026-11-19"
    assert d["quarantined"] == {"non_canonical_ts": 1}
    assert d["flagged"] == {"large_gap": 1}
    m = p["minute"]
    assert m["analysis_start"] == "2016-01-01"
    assert (m["coverage"]["sessions"], m["coverage"]["bars"]) == (2, 60)
    assert p["as_traded_days"] == {"measured": len(DAYS)}


async def test_index_has_no_actions_futures_or_tick(client: Any) -> None:
    p = (await _get(client, "NSE:NIFTY 50")).json()
    assert (p["kind"], p["futures"], p["tick_size_paisa"], p["actions"]) == (
        "index",
        None,
        None,
        [],
    )
    assert p["minute"]["coverage"] is None  # no 1m stored for it here: absent, not zero


async def test_unknown_symbol_is_404(client: Any) -> None:
    r = await _get(client, "NSE:NOPE")
    assert r.status_code == 404


async def test_analysis_ignores_bond_and_preference_series(client: Any, db: Any) -> None:
    acts = await load_actions(db, "NSE:AAA")
    assert sorted(a.purpose for a in acts) == [
        "BONUS 1:1",
        "DIV - RS 4 PER SH",
        "DIV - RS 5 PER SH",
    ]
