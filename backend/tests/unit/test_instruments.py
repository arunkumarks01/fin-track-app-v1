from __future__ import annotations

from datetime import date

import pytest

from app.data.instruments import (
    InstrumentsError,
    UniverseMember,
    build_universe,
    parse_instruments_csv,
)
from tests.conftest import build_settings
from tests.fixtures.instruments import instruments_csv

INDICES = build_settings().universe_index_symbols


def test_parse_keeps_relevant_exchanges_in_paisa() -> None:
    rows = parse_instruments_csv(instruments_csv())
    symbols = {r.symbol for r in rows}
    assert "BSE:SENSEX" in symbols
    assert "BSE:RELIANCE" not in symbols  # BSE equities dropped
    assert "MCX:CRUDEOIL26DECFUT" not in symbols
    rel = next(r for r in rows if r.symbol == "NSE:RELIANCE")
    assert (rel.instrument_token, rel.tick_size, rel.lot_size, rel.strike) == (738561, 10, 1, None)
    opt = next(r for r in rows if r.tradingsymbol == "NIFTY26NOV24500CE")
    assert (opt.strike, opt.tick_size, opt.expiry, opt.lot_size) == (
        2_450_000,
        5,
        date(2026, 11, 26),
        75,
    )


@pytest.mark.parametrize(
    "bad_row",
    [
        "x,1,BAD,BAD,0,,,0.05,1,EQ,NSE,NSE",  # non-int token
        "1,1,BAD,BAD,0,2026-13-40,,0.05,1,EQ,NSE,NSE",  # bad expiry
        "1,1,BAD,BAD,0,,,0.005,1,EQ,NSE,NSE",  # sub-paisa tick
    ],
)
def test_malformed_relevant_row_fails_closed(bad_row: str) -> None:
    with pytest.raises(InstrumentsError, match="malformed"):
        parse_instruments_csv(instruments_csv(bad_row))


def test_malformed_irrelevant_row_ignored() -> None:
    parse_instruments_csv(instruments_csv("x,1,JUNK,JUNK,0,,,abc,1,FUT,MCX-FUT,MCX"))


def test_unexpected_header_fails_closed() -> None:
    with pytest.raises(InstrumentsError, match="header"):
        parse_instruments_csv(instruments_csv(header="token,symbol"))


def test_universe_is_fno_stocks_plus_configured_indices() -> None:
    members = build_universe(parse_instruments_csv(instruments_csv()), INDICES)
    assert [m for m in members if m.kind == "equity"] == [
        UniverseMember("NSE:INFY", 408065, "equity"),
        UniverseMember("NSE:RELIANCE", 738561, "equity"),
        UniverseMember("NSE:TCS", 2953217, "equity"),
    ]  # ABB (no F&O) and NIFTY (index future, no NSE EQ) excluded
    assert [m.symbol for m in members if m.kind == "index"] == INDICES


def test_missing_configured_index_fails_closed() -> None:
    with pytest.raises(InstrumentsError, match="NSE:NIFTY NEXT 50"):
        build_universe(parse_instruments_csv(instruments_csv()), [*INDICES, "NSE:NIFTY NEXT 50"])
