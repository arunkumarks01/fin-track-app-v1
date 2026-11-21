"""1-minute extractor on the real DB: newest-first rounds, resumable, budget fail-closed."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, time
from typing import Any

import asyncpg
import pytest

from app.broker.kite.client import KiteAuthError, KiteUnavailableError
from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ
from app.data import extract_intraday
from app.data.extract_daily import run_daily_backfill
from app.data.extract_intraday import run_intraday_backfill
from app.data.instruments import (
    GIFT_NIFTY_SYMBOL,
    INDIA_VIX_SYMBOL,
    UniverseMember,
    parse_instruments_csv,
    replace_instruments,
    sync_universe,
)
from app.data.reference_report import reference_counts
from app.db.migrate import migrate
from tests.conftest import build_settings
from tests.fixtures.clock import FrozenClock
from tests.fixtures.historical import FakeHistorical
from tests.fixtures.instruments import instruments_csv
from tests.fixtures.minutes import session, weekdays

pytestmark = pytest.mark.db

IST = MARKET_TZ
FLOOR = date(2026, 9, 1)
END = date(2026, 11, 18)  # a Wednesday
EVENING = datetime(2026, 11, 18, 18, 0, tzinfo=IST)
MINUTES = 20  # short synthetic sessions keep the tests fast
MEMBERS = [
    UniverseMember("NSE:AAA", 101, "equity"),
    UniverseMember("NSE:BBB", 202, "equity"),
    UniverseMember("NSE:NIFTY 50", 256265, "index"),
]


def settings_for(test_dsn: str, **kw: Any) -> Settings:
    base: dict[str, Any] = {
        "intraday_floor_date": FLOOR,
        "kite_window_days": {"minute": 10},
        "intraday_db_budget_gb": 1000.0,
    }
    base.update(kw)
    return build_settings(database_url=test_dsn, **base)


def history(token: int, start: date = FLOOR, end: date = END) -> list[list[Any]]:
    return [r for d in weekdays(start, end) for r in session(d, minutes=MINUTES, seed=token)]


def full_source() -> FakeHistorical:
    return FakeHistorical({101: history(101), 202: history(202), 256265: history(256265)})


@pytest.fixture
async def pool(test_dsn: str, db: Any) -> AsyncIterator[Any]:
    await migrate(db)
    await sync_universe(db, MEMBERS, EVENING)
    p = await asyncpg.create_pool(test_dsn, min_size=1, max_size=3)
    try:
        yield p
    finally:
        await p.close()


async def _rows(db: Any, symbol: str) -> int:
    return int(
        await db.fetchval(
            """SELECT count(*) FROM app.ohlcv_1m m JOIN app.instrument_ids i USING (sid)
           WHERE i.symbol = $1""",
            symbol,
        )
    )


async def test_reference_indices_use_their_own_finality_outside_the_stock_universe(
    pool: Any, db: Any, test_dsn: str
) -> None:
    symbol, token = GIFT_NIFTY_SYMBOL, 291849
    vix_token = 264969
    master = instruments_csv(
        "291849,1140,GIFT NIFTY,GIFT NIFTY,0,,,0,0,EQ,INDICES,NSEIX",
        "264969,1035,INDIA VIX,INDIA VIX,0,,,0,0,EQ,INDICES,NSE",
    )
    await replace_instruments(db, parse_instruments_csv(master), EVENING)
    prior = date(2026, 11, 17)
    cfg = settings_for(
        test_dsn,
        ingest_daily_start=prior,
        intraday_floor_date=prior,
        kite_window_days={"day": 30, "minute": 10},
    )
    clock = FrozenClock(EVENING)
    daily = FakeHistorical(
        {token: session(prior, start=time.min, minutes=1) + session(END, start=time.min, minutes=1)}
    )
    minute = FakeHistorical(
        {
            token: session(prior, start=time(22), minutes=2)
            + session(END, start=time(16), minutes=2),
            vix_token: session(END, minutes=3),
        }
    )
    await run_daily_backfill(pool, daily, cfg, clock, [symbol], include_reference_indices=True)
    await run_intraday_backfill(
        pool, minute, cfg, clock, [symbol, INDIA_VIX_SYMBOL], include_reference_indices=True
    )

    [daily_call] = daily.calls
    assert (daily_call[0], daily_call[3].date()) == (token, prior)
    assert {call[0]: call[3].date() for call in minute.calls} == {token: prior, vix_token: END}
    assert not await db.fetchval(
        "SELECT EXISTS (SELECT 1 FROM app.universe WHERE symbol = ANY($1::text[]))",
        [symbol, INDIA_VIX_SYMBOL],
    )
    counts = await reference_counts(db, END)
    coverage = counts["gift_nifty"]
    assert coverage["daily_bars"] == 1
    assert coverage["daily_date"] == prior.isoformat()
    assert coverage["minute_bars"] == 2
    assert datetime.fromisoformat(coverage["minute_at"]).astimezone(IST).hour == 22
    assert counts["india_vix"]["minute_bars"] == 3


async def test_newest_first_rounds_then_idempotent(pool: Any, db: Any, test_dsn: str) -> None:
    src = full_source()
    report = await run_intraday_backfill(pool, src, settings_for(test_dsn), FrozenClock(EVENING))
    assert report.stopped_reason is None
    expected = len(weekdays(FLOOR, END)) * MINUTES
    for m in MEMBERS:
        assert await _rows(db, m.symbol) == expected
    # round 1 = every symbol's NEWEST window before anyone's older window
    ends = [c[3].date() for c in src.calls]
    assert ends[:3] == [END, END, END]
    assert ends[3:6] == [date(2026, 11, 8)] * 3
    # windows walk strictly backwards per symbol, down to the floor
    starts = sorted({c[2].date() for c in src.calls})
    assert starts[0] == FLOOR
    calls = len(src.calls)

    again = await run_intraday_backfill(pool, src, settings_for(test_dsn), FrozenClock(EVENING))
    assert again.inserted == 0
    # everything floored + up to date: only the drift re-check of each symbol's newest
    # stored session (one request per symbol), recorded as checked
    rechecks = src.calls[calls:]
    assert len(rechecks) == 3
    assert {(c[2].date(), c[3].date()) for c in rechecks} == {(END, END)}
    assert again.already_stored == 3 * MINUTES
    assert (
        await db.fetchval(
            "SELECT count(*) FROM app.adjustment_checks WHERE timeframe = '1m' AND checked_on = $1",
            END,
        )
        == 3
    )
    dupes = await db.fetchval("SELECT count(*) - count(DISTINCT (sid, ts)) FROM app.ohlcv_1m")
    assert dupes == 0


async def test_session_calendar_derived_from_benchmark(pool: Any, db: Any, test_dsn: str) -> None:
    src = full_source()
    # AAA is missing 3 minutes on the last day; the benchmark is complete
    src.series[101] = history(101, end=date(2026, 11, 17)) + session(
        END, minutes=MINUTES, seed=101, skip=frozenset({0, 5, 6})
    )
    await run_intraday_backfill(pool, src, settings_for(test_dsn), FrozenClock(EVENING))
    row = await db.fetchrow(
        "SELECT session_open, expected_minutes, benchmark FROM app.exchange_sessions "
        "WHERE exchange = 'NSE' AND session_date = $1",
        END,
    )
    assert row["expected_minutes"] == MINUTES  # from NIFTY 50, not from the short symbol
    assert row["benchmark"] == "NSE:NIFTY 50"
    assert row["session_open"] == datetime.combine(END, time(9, 15), IST)  # earliest open
    bars = await db.fetchval(
        """SELECT bars FROM app.intraday_sessions s JOIN app.instrument_ids i USING (sid)
           WHERE i.symbol = 'NSE:AAA' AND session_date = $1""",
        END,
    )
    assert bars == MINUTES - 3


async def test_budget_fails_closed_before_any_fetch(pool: Any, db: Any, test_dsn: str) -> None:
    src = full_source()
    report = await run_intraday_backfill(
        pool, src, settings_for(test_dsn, intraday_db_budget_gb=0.001), FrozenClock(EVENING)
    )
    assert report.stopped_reason is not None
    assert "disk budget reached" in report.stopped_reason
    assert src.calls == []
    assert await db.fetchval("SELECT count(*) FROM app.ohlcv_1m") == 0


async def test_budget_mid_run_stops_then_resumes_cleanly(
    pool: Any, db: Any, test_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sizes = iter([1.0, 1.0, 1.0, 9.0])  # start check, round-1 check, 1 window check, then over

    async def fake_size(_conn: Any) -> float:
        return next(sizes, 9.0)

    monkeypatch.setattr(extract_intraday, "db_size_gb", fake_size)
    s = settings_for(test_dsn, intraday_db_budget_gb=5.0, intraday_budget_check_every=2)
    src = full_source()
    report = await run_intraday_backfill(pool, src, s, FrozenClock(EVENING))
    assert report.stopped_reason is not None
    assert "disk budget reached" in report.stopped_reason
    partial = await db.fetchval("SELECT count(*) FROM app.ohlcv_1m")
    assert 0 < partial < 3 * len(weekdays(FLOOR, END)) * MINUTES
    # the newest data landed first: every stored day is within the newest window
    oldest = await db.fetchval("SELECT min(ts) FROM app.ohlcv_1m")
    assert oldest.astimezone(IST).date() >= date(2026, 11, 9)

    monkeypatch.undo()
    resumed = await run_intraday_backfill(pool, src, settings_for(test_dsn), FrozenClock(EVENING))
    assert resumed.stopped_reason is None
    assert await db.fetchval("SELECT count(*) FROM app.ohlcv_1m") == (
        3 * len(weekdays(FLOOR, END)) * MINUTES
    )


async def test_late_listing_floors_after_empty_windows(pool: Any, db: Any, test_dsn: str) -> None:
    src = full_source()
    src.series[202] = history(202, start=date(2026, 11, 2))  # listed 2 Nov
    await run_intraday_backfill(pool, src, settings_for(test_dsn), FrozenClock(EVENING))
    bbb_calls = [c for c in src.calls if c[0] == 202]
    # 2 windows with data, then 3 empty windows -> floor; never walks to 1 Sep
    assert len(bbb_calls) == 5
    cur = await db.fetchrow(
        """SELECT floor_reached, empty_windows FROM app.intraday_cursor c
           JOIN app.instrument_ids i USING (sid) WHERE i.symbol = 'NSE:BBB'"""
    )
    assert (cur["floor_reached"], cur["empty_windows"]) == (True, 3)
    assert await _rows(db, "NSE:BBB") == len(weekdays(date(2026, 11, 2), END)) * MINUTES


async def test_symbol_error_skips_it_others_complete(pool: Any, db: Any, test_dsn: str) -> None:
    src = full_source()
    src.fail_on_call = {2: KiteUnavailableError("503s")}  # order: NIFTY 50, AAA, BBB
    report = await run_intraday_backfill(pool, src, settings_for(test_dsn), FrozenClock(EVENING))
    assert set(report.errors) == {"NSE:BBB"}
    assert await _rows(db, "NSE:BBB") == 0
    assert await _rows(db, "NSE:AAA") == len(weekdays(FLOOR, END)) * MINUTES
    src.fail_on_call.clear()
    await run_intraday_backfill(pool, src, settings_for(test_dsn), FrozenClock(EVENING))
    assert await _rows(db, "NSE:BBB") == len(weekdays(FLOOR, END)) * MINUTES


async def test_auth_error_stops_everything(pool: Any, db: Any, test_dsn: str) -> None:
    src = full_source()
    src.fail_on_call = {0: KiteAuthError("expired")}
    report = await run_intraday_backfill(pool, src, settings_for(test_dsn), FrozenClock(EVENING))
    assert report.stopped_reason is not None
    assert "expired" in report.stopped_reason
    # windows already in flight in round 1 may finish; nothing beyond round 1 is requested
    assert 1 <= len(src.calls) <= len(MEMBERS)
    assert all(c[3].date() == END for c in src.calls)


async def test_concurrency_one_is_strictly_sequential(pool: Any, db: Any, test_dsn: str) -> None:
    src = full_source()
    src.fail_on_call = {0: KiteAuthError("expired")}
    s = settings_for(test_dsn, intraday_concurrency=1)
    await run_intraday_backfill(pool, src, s, FrozenClock(EVENING))
    assert len(src.calls) == 1  # with one worker the stop is immediate


async def test_forward_catch_up_fetches_only_new_days(pool: Any, db: Any, test_dsn: str) -> None:
    src = FakeHistorical(
        {
            101: history(101, end=date(2026, 11, 10)),
            202: history(202, end=date(2026, 11, 10)),
            256265: history(256265, end=date(2026, 11, 10)),
        }
    )
    s = settings_for(test_dsn)
    await run_intraday_backfill(
        pool, src, s, FrozenClock(datetime(2026, 11, 10, 18, 0, tzinfo=IST))
    )
    before = len(src.calls)
    src.series = {t: history(t) for t in (101, 202, 256265)}  # 6 more sessions appear
    report = await run_intraday_backfill(pool, src, s, FrozenClock(EVENING))
    new_calls = src.calls[before:]
    assert len(new_calls) == 3  # one catch-up window per symbol, nothing else
    # it starts ON the newest stored session (11-10, the drift re-check), not after it
    assert all(c[2].date() == date(2026, 11, 10) for c in new_calls)
    assert report.inserted == 3 * 6 * MINUTES
    assert report.already_stored == 3 * MINUTES


async def test_overflowing_volume_is_quarantined_not_fatal(
    pool: Any, db: Any, test_dsn: str
) -> None:
    """Regression: Kite served volume 2**64-2 (NSE:COROMANDEL 2015-03-13 09:15) and the COPY
    crashed the whole 4-hour run. It must be quarantined and the run must finish."""
    src = full_source()
    bad = src.series[101][5]
    bad[5] = 18446744073709551614
    report = await run_intraday_backfill(pool, src, settings_for(test_dsn), FrozenClock(EVENING))
    assert report.stopped_reason is None
    assert report.errors == {}
    expected = len(weekdays(FLOOR, END)) * MINUTES
    assert await _rows(db, "NSE:AAA") == expected - 1
    assert await _rows(db, "NSE:BBB") == expected
    rows = await db.fetch(
        "SELECT symbol, ts, reason FROM app.ohlcv_quarantine WHERE timeframe = '1m'"
    )
    assert [(r["symbol"], r["reason"]) for r in rows] == [("NSE:AAA", "out_of_range")]


async def test_missing_sessions_counts_only_gaps_inside_each_symbols_span(
    pool: Any, db: Any, test_dsn: str
) -> None:
    from app.data.intraday_coverage import completeness

    src = full_source()
    gap = date(2026, 10, 14)  # a Wednesday inside AAA's span
    listed = date(2026, 10, 1)  # AAA lists late: sessions before this are not "missing"
    src.series[101] = [
        r for d in weekdays(listed, END) if d != gap for r in session(d, minutes=MINUTES, seed=101)
    ]
    await run_intraday_backfill(pool, src, settings_for(test_dsn), FrozenClock(EVENING))
    by = {c.symbol: c for c in await completeness(db)}
    assert by["NSE:AAA"].first == listed
    assert by["NSE:AAA"].missing_sessions == 1
    assert by["NSE:AAA"].review == 1
    assert by["NSE:BBB"].missing_sessions == 0
    assert by["NSE:NIFTY 50"].missing_sessions == 0


async def test_sessions_nse_says_were_not_traded_are_not_missing_data(
    pool: Any, db: Any, test_dsn: str
) -> None:
    """A suspension (FORCEMOT 2023-10-26 → 2024-02-13) is not a data gap: an equity session with
    no 1m bars is 'not traded' only when NSE's archive for that day loaded and has no row for
    the symbol. Otherwise — NSE traded it, the archive can't vouch, or it's an index — it stays
    missing."""
    from app.data.intraday_coverage import completeness

    src = full_source()
    suspended = date(2026, 10, 14)  # archive ok, no NSE row → not traded
    traded = date(2026, 10, 15)  # archive ok, NSE row exists → our data is missing
    unvouched = date(2026, 10, 16)  # archive day missing → can't vouch → missing
    holes = {suspended, traded, unvouched}
    src.series[101] = [
        r
        for d in weekdays(FLOOR, END)
        if d not in holes
        for r in session(d, minutes=MINUTES, seed=101)
    ]
    src.series[256265] = [  # the index loses the 'suspended' day too: never excused
        r
        for d in weekdays(FLOOR, END)
        if d != suspended
        for r in session(d, minutes=MINUTES, seed=7)
    ]
    await run_intraday_backfill(pool, src, settings_for(test_dsn), FrozenClock(EVENING))
    for d, status in ((suspended, "ok"), (traded, "ok"), (unvouched, "missing")):
        await db.execute(
            "INSERT INTO app.nse_archive_files (trade_date, status, fetched_at)"
            " VALUES ($1, $2, now())",
            d,
            status,
        )
    await db.execute(
        """INSERT INTO app.nse_bhav (symbol, trade_date, prev_close, open, high, low, close, volume)
           VALUES ('NSE:AAA', $1, 100, 100, 101, 99, 100, 10)""",
        traded,
    )
    by = {c.symbol: c for c in await completeness(db)}
    assert (by["NSE:AAA"].missing_sessions, by["NSE:AAA"].not_traded_sessions) == (2, 1)
    assert by["NSE:AAA"].review == 2
    assert (by["NSE:NIFTY 50"].missing_sessions, by["NSE:NIFTY 50"].not_traded_sessions) == (1, 0)


async def test_intraday_drift_is_recorded_when_a_whole_session_rescales(
    pool: Any, db: Any, test_dsn: str
) -> None:
    src = FakeHistorical({101: history(101, end=date(2026, 11, 10))})
    s = settings_for(test_dsn)
    await run_intraday_backfill(
        pool, src, s, FrozenClock(datetime(2026, 11, 10, 18, 0, tzinfo=IST)), ["NSE:AAA"]
    )
    # Kite re-adjusts AAA's history x0.5 (prices only); the newest stored session comes back
    from decimal import Decimal

    def half(x: Any) -> Decimal:  # Kite rounds its adjusted prices to the paisa
        return (Decimal(x) / 2).quantize(Decimal("0.01"))

    halved = [
        [r[0], *(half(x) for x in r[1:5]), r[5]] for r in history(101, end=date(2026, 11, 10))
    ]
    src.series[101] = halved
    report = await run_intraday_backfill(
        pool, src, s, FrozenClock(datetime(2026, 11, 11, 18, 0, tzinfo=IST)), ["NSE:AAA"]
    )
    assert report.quarantined == 0  # not conflicts: one consistent ratio
    drift = await db.fetchrow("SELECT timeframe, ref_date, ratio FROM app.adjustment_drift")
    assert (drift["timeframe"], drift["ref_date"]) == ("1m", date(2026, 11, 10))
    assert float(drift["ratio"]) == pytest.approx(0.5, abs=1e-3)
