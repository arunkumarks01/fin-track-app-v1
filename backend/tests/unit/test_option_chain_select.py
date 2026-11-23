"""Option-chain scope: which strikes are snapshotted, and how a quote becomes a stored row."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.data.option_chain import Contract, _paisa, _when, option_row, select_strikes

E1, E2, E3 = date(2026, 11, 27), date(2026, 12, 3), date(2026, 12, 31)


def chain(expiry: date, lo: int, hi: int, step: int) -> list[Contract]:
    out = []
    for strike in range(lo, hi + 1, step):
        for t in ("CE", "PE"):
            out.append(
                Contract(
                    strike * 10 + (t == "PE"), f"X{expiry:%d%b}{strike}{t}", expiry, strike * 100, t
                )
            )
    return out


def test_atm_plus_minus_n_strikes_for_the_first_two_expiries_calls_and_puts() -> None:
    cs = chain(E1, 24000, 27000, 100) + chain(E2, 24000, 27000, 100) + chain(E3, 24000, 27000, 100)
    got = select_strikes(
        cs, spot_paisa=25_512_00, expiries=2, each_side=15
    )  # spot 25,512 -> ATM 25,500
    assert {c.expiry for c in got} == {E1, E2}  # the third expiry is out of scope
    per = [c for c in got if c.expiry == E1 and c.opt_type == "CE"]
    assert len(per) == 31  # ATM + 15 each side
    assert min(c.strike_paisa for c in per) == 24_000 * 100 + 0  # 25,500 - 1,500 = 24,000
    assert max(c.strike_paisa for c in per) == 27_000 * 100
    assert len([c for c in got if c.expiry == E1]) == 62  # both calls and puts


def test_near_the_edge_of_the_listed_strikes_it_takes_what_exists_and_ties_go_lower() -> None:
    cs = chain(E1, 100, 300, 100)  # strikes 100, 200, 300
    low = select_strikes(cs, spot_paisa=90_00, expiries=1, each_side=1)
    assert sorted({c.strike_paisa for c in low}) == [10_000, 20_000]  # ATM 100 and one above
    tie = select_strikes(cs, spot_paisa=150_00, expiries=1, each_side=0)
    assert sorted({c.strike_paisa for c in tie}) == [10_000]  # 150 is equidistant: the lower one
    assert select_strikes([], 100, 2, 15) == []


def test_prices_are_exact_paisa_or_nothing() -> None:
    assert _paisa(Decimal("141.30")) == 14130
    assert _paisa(Decimal("0.05")) == 5
    assert _paisa(Decimal("141.305")) is None  # more than a paisa: not representable, never rounded
    assert _paisa(None) is None
    assert _paisa(Decimal("99999999999")) is None  # beyond the column


def test_times_are_ist_and_a_zero_date_means_none() -> None:
    assert _when("2026-11-23 10:05:09") == datetime(2026, 11, 23, 4, 35, 9, tzinfo=UTC)
    assert _when("0001-01-01 00:00:00") is None
    assert _when("garbage") is None
    assert _when(None) is None


def test_a_quote_becomes_one_raw_row_and_a_priceless_quote_becomes_none() -> None:
    c = Contract(5, "NIFTY26NOV25500CE", E1, 25_500_00, "CE")
    q = {
        "last_price": Decimal("120.45"),
        "volume": 5000,
        "oi": 90000,
        "last_trade_time": "2026-11-23 10:05:09",
        "depth": {
            "buy": [{"price": Decimal("120.40"), "quantity": 75, "orders": 3}],
            "sell": [{"price": Decimal("120.50"), "quantity": 150, "orders": 2}],
        },
    }
    ts = datetime(2026, 11, 23, 10, 5, tzinfo=UTC)
    row = option_row(ts, "NIFTY", c, q)
    assert row is not None
    assert row[:9] == (ts, "NIFTY", 5, E1, 25_500_00, "CE", 12045, 5000, 90000)
    assert row[9:13] == (12040, 75, 12050, 150)
    assert option_row(ts, "NIFTY", c, {"volume": 1}) is None
    thin = option_row(
        ts, "NIFTY", c, {"last_price": Decimal("1.00"), "depth": {"buy": [], "sell": []}}
    )
    assert thin is not None
    assert thin[9:13] == (None, None, None, None)  # no market: no bid or ask, never zeros
