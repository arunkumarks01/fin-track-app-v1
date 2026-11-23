"""Slice 1f parsers — fixtures are verbatim lines of real NSE F&O files (2015 and 2026)."""
# ruff: noqa: E501

from __future__ import annotations

from datetime import date

import pytest

from app.data.nse_archive import NseArchiveError, NseArchiveMislabeled
from app.data.nse_fo import fo_url, parse_fo

OLD = """INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,
FUTIDX,BANKNIFTY,29-Jan-2015,0,XX,19270.05,19281,19110,19150.15,19150.15,84356,404482.59,2013850,3275,05-JAN-2015,
FUTSTK,MOTHERSUMI,26-Feb-2015,0,XX,463.05,465.45,458,457,457,33,76.22,36000,-500,05-JAN-2015,
OPTSTK,GVT&D,29-Jan-2015,2.5,CE,0,0,0,0,0,0,0,0,0,05-JAN-2015,
OPTIDX,NIFTY,29-Jan-2015,8500,PE,12.5,14,9.05,10.35,10.35,5000,1000.5,120000,-300,05-JAN-2015,
FUTIDX,NIFTY,26-Feb-2015,0,XX,9,8,9,9,9,10,1,100,0,05-JAN-2015,
FUTSTK,BADROW,26-Feb-2015,0,XX,abc,1,1,1,1,1,1,1,0,05-JAN-2015,
"""

NEW = """TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4
2026-11-20,2026-11-20,FO,NSE,STO,70776,,ABCAPITAL,,2026-12-29,2026-12-29,470.00,CE,ABCAPITAL26DEC470CE,2.15,3.10,2.00,2.60,2.60,1.75,423.00,2.60,46500,40300,80,58605887.50,49,F1,1550,,,,,
2026-11-20,2026-11-20,FO,NSE,STF,58880,,ABCAPITAL,,2026-12-29,2026-12-29,,,ABCAPITAL26DECFUT,417.00,427.00,414.50,424.40,424.30,420.60,423.00,424.40,25277400,6851000,7053,4624276975.00,5666,F1,1550,,,,,
2026-11-20,2026-11-20,FO,NSE,STO,68455,,ABCAPITAL,,2027-01-25,2027-01-25,470.00,CE,ABCAPITAL27JAN470CE,0.00,0.00,0.00,3.05,0.00,3.05,423.00,9.22,0,0,0,0.00,0,F1,1550,,,,,
"""


def test_old_format_rows_normalise_and_idle_rows_are_skipped() -> None:
    f = parse_fo(OLD, date(2015, 1, 5))
    assert [(r.underlying, r.kind, r.opt_type, r.strike_paisa) for r in f.rows] == [
        ("BANKNIFTY", "IDF", "FUT", 0),
        ("MOTHERSUMI", "STF", "FUT", 0),
        ("NIFTY", "IDO", "PE", 850000),
    ]
    assert f.rows[0].close == 1915015  # 19150.15 -> exact paisa
    assert f.rows[0].lot_size is None  # not in the old format
    assert f.idle == 1  # the GVT&D strike with no trades and no open interest


def test_out_of_range_close_is_kept_and_counted_impossible_rows_are_rejected() -> None:
    f = parse_fo(OLD, date(2015, 1, 5))
    assert f.odd_range == 1  # MOTHERSUMI: close 457 below its low 458 (NSE's own quirk), kept
    assert f.rejected == 2  # 'abc' is not a price; NIFTY futures with high 8 below low 9


def test_udiff_format_keeps_spot_previous_close_and_lot_size() -> None:
    f = parse_fo(NEW, date(2026, 11, 20))
    opt, fut = f.rows
    assert (opt.underlying, opt.kind, opt.opt_type, opt.strike_paisa) == (
        "ABCAPITAL",
        "STO",
        "CE",
        47000,
    )
    assert (opt.close, opt.prev_close, opt.underlying_price, opt.lot_size) == (
        260,
        175,
        42300,
        1550,
    )
    assert (fut.kind, fut.opt_type, fut.strike_paisa, fut.open_interest) == (
        "STF",
        "FUT",
        0,
        25277400,
    )
    assert f.idle == 1


def test_a_file_for_another_day_is_refused() -> None:
    with pytest.raises(NseArchiveMislabeled):
        parse_fo(NEW, date(2026, 11, 19))
    with pytest.raises(NseArchiveMislabeled):
        parse_fo(OLD, date(2015, 1, 6))
    with pytest.raises(NseArchiveError, match="columns"):
        parse_fo("a,b,c\n1,2,3\n", date(2015, 1, 5))


def test_urls_follow_each_formats_pattern() -> None:
    old = "https://h/old"
    new = "https://h/new/"
    cut = date(2024, 1, 2)
    assert (
        fo_url(old, new, cut, date(2015, 1, 5)) == "https://h/old/2015/JAN/fo05JAN2015bhav.csv.zip"
    )
    assert (
        fo_url(old, new, cut, date(2024, 1, 2))
        == "https://h/new/BhavCopy_NSE_FO_0_0_0_20240102_F_0000.csv.zip"
    )
