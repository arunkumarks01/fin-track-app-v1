"""Renames + trade-for-trade series (Phase 1c close-out). Real lines from NSE's archive."""

# ruff: noqa: E501 — the CSV header is NSE's verbatim

from __future__ import annotations

from datetime import date

from app.data.nse_archive import names_on, parse_bc, parse_pd, parse_symbol_changes

CHANGES_CSV = """NAME OF THE COMPANY,OLD SYMBOL,NEW SYMBOL,EFFECTIVE DATE
360 ONE WAM LIMITED,IIFLWAM,360ONE,23-JAN-2023
FORCE MOTORS LTD,BAJAJTEMPO,FORCEMOT,19-AUG-2019
Eternal Limited,ZOMATO,ETERNAL,09-APR-2025
Some Co,AAA1,AAA2,01-JAN-2018
Some Co,AAA2,AAA3,01-JAN-2022
 junk row without a date,X,Y,
"""

UNIVERSE = {"NSE:360ONE", "NSE:ETERNAL", "NSE:AAA3", "NSE:JIOFIN", "NSE:TCS"}


def test_symbol_change_file_parses_and_skips_junk() -> None:
    rows = parse_symbol_changes(CHANGES_CSV)
    assert ("IIFLWAM", "360ONE", date(2023, 1, 23)) in [(r.old, r.new, r.effective) for r in rows]
    assert len(rows) == 5


def test_names_follow_renames_backwards_including_chains() -> None:
    ch = parse_symbol_changes(CHANGES_CSV)
    before = names_on(date(2022, 12, 1), UNIVERSE, ch)
    assert before["IIFLWAM"] == "NSE:360ONE"
    assert "360ONE" not in before  # the new name didn't exist yet
    after = names_on(date(2023, 1, 23), UNIVERSE, ch)
    assert after["360ONE"] == "NSE:360ONE"
    assert "IIFLWAM" not in after
    assert names_on(date(2017, 6, 1), UNIVERSE, ch)["AAA1"] == "NSE:AAA3"  # two hops back
    assert names_on(date(2020, 6, 1), UNIVERSE, ch)["AAA2"] == "NSE:AAA3"
    assert names_on(date(2020, 6, 1), UNIVERSE, ch)["TCS"] == "NSE:TCS"


PD = """MKT,SERIES,SYMBOL,SECURITY,PREV_CL_PR,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,CLOSE_PRICE,NET_TRDVAL,NET_TRDQTY,IND_SEC,CORP_IND,TRADES,HI_52_WK,LO_52_WK
N,EQ,IIFLWAM,IIFL WEALTH,100.00,101.00,102.00,99.00,100.50,1.0,10,N, ,1,1,1
N,BE,JIOFIN,JIO FINANCIAL,262.00,265.00,266.00,250.00,251.60,1.0,99,N, ,1,1,1
N,EQ,TCS,TCS LTD,3000.00,3001.00,3010.00,2990.00,3005.00,1.0,5,N, ,1,1,1
N,BE,TCS,TCS LTD,3000.00,3100.00,3110.00,3090.00,3105.00,1.0,5,N, ,1,1,1
N,T0,TCS,TCS LTD,3000.00,3100.00,3110.00,3090.00,3105.00,1.0,5,N, ,1,1,1
N,N1,JIOFIN,SOME BOND,100.00,100.00,100.00,100.00,100.00,1.0,5,N, ,1,1,1
"""


def test_pd_maps_old_names_and_falls_back_to_trade_for_trade_series() -> None:
    names = names_on(date(2022, 12, 1), UNIVERSE, parse_symbol_changes(CHANGES_CSV))
    p = parse_pd(PD, date(2022, 12, 1), names)
    by = {r.symbol: r for r in p.rows}
    assert (by["NSE:360ONE"].source_symbol, by["NSE:360ONE"].series, by["NSE:360ONE"].close) == (
        "IIFLWAM",
        "EQ",
        10050,
    )
    assert (by["NSE:JIOFIN"].series, by["NSE:JIOFIN"].close) == ("BE", 25160)  # listing days
    assert (by["NSE:TCS"].series, by["NSE:TCS"].close) == ("EQ", 300500)  # EQ wins over BE/T0
    assert p.rejected == 0


def test_bc_maps_old_names_to_the_current_symbol() -> None:
    bc = (
        "SERIES,SYMBOL,SECURITY,RECORD_DT,BC_STRT_DT,BC_END_DT,EX_DT,ND_STRT_DT,ND_END_DT,PURPOSE\n"
        "EQ,IIFLWAM,IIFL Wealth,10/12/2022, , ,09/12/2022, , ,INTDIV - RS 17 PER SH\n"
    )
    names = names_on(date(2022, 12, 1), UNIVERSE, parse_symbol_changes(CHANGES_CSV))
    rows = parse_bc(bc, names).rows
    assert [(r.symbol, r.ex_date, r.dividend_paisa) for r in rows] == [
        ("NSE:360ONE", date(2022, 12, 9), 1700)
    ]
