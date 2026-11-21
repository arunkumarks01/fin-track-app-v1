# ruff: noqa: E501 — the fixtures are verbatim lines of real NSE archive files
"""Phase 1e step 1 parsers — fixtures are trimmed copies of real archive files (2015 and 2026)."""

from __future__ import annotations

from datetime import date

import pytest

from app.data.nse_archive import NseArchiveError, NseArchiveMislabeled
from app.data.nse_reference import (
    FIELDS,
    mto_url,
    parse_market_cap,
    parse_mto,
    parse_participants,
    participant_url,
)

MCAP_2026 = """Trade Date,Symbol,Series,Security Name,Category,Last Trade Date,Face Value(Rs.),Issue Size,Close Price/Paid up value(Rs.),Market Cap(Rs.)
20 NOV 2026,RELIANCE,EQ,RELIANCE INDUSTRIES LTD  ,Listed    ,20 NOV 2026,              10.00,       13532538722,           1377.50, 18641072089555.00
20 NOV 2026,SHRIRAMFIN,EQ,SHRIRAM FINANCE LIMITED  ,Listed    ,20 NOV 2026,               2.00,        2353045034,            972.00,  2287159773048.00
"""


def test_market_cap_uses_published_values_and_refuses_wrong_dates() -> None:
    d = date(2026, 11, 20)
    names = {"RELIANCE": "NSE:RELIANCE", "SHRIRAMFIN": "NSE:SHRIRAMFIN"}
    rows = [line for line in MCAP_2026.splitlines() if "RELIANCE" in line]
    [reliance] = rows
    alternate = reliance.replace(",EQ,", ",BE,").replace("18641072089555.00", "1.01")
    ignored = reliance.replace(",EQ,", ",N1,")
    data = MCAP_2026 + alternate + "\n" + ignored + "\n"
    parsed = parse_market_cap(data, d, names)
    assert parsed.rejected == 0
    assert [(r.symbol, r.series, r.issue_size, r.market_cap_paisa) for r in parsed.rows] == [
        ("NSE:RELIANCE", "EQ", 13_532_538_722, 1_864_107_208_955_500),
        ("NSE:SHRIRAMFIN", "EQ", 2_353_045_034, 228_715_977_304_800),
    ]
    inexact = parse_market_cap(data.replace("2287159773048.00", "1.001"), d, names)
    assert (len(inexact.rows), inexact.rejected) == (1, 1)
    with pytest.raises(NseArchiveMislabeled):
        parse_market_cap(data.replace("20 NOV 2026,SHRIRAMFIN", "19 NOV 2026,SHRIRAMFIN"), d, names)


def test_participant_archive_titles_can_omit_the_space_after_the_date_comma() -> None:
    d = date(2025, 5, 2)
    text = (
        VOL_2015.replace("Jan 05, 2015", "May 02,2025")
        .replace('"Participant', '""Participant', 1)
        .replace('2025",', '2025"",', 1)
    )
    assert len(parse_participants(text, d, "volume").rows) == 4
    with pytest.raises(NseArchiveMislabeled):
        parse_participants(text, date(2025, 5, 5), "volume")


MTO_2015 = """Security Wise Delivery Position - Compulsory Rolling Settlement
10,MTO,05012015,298317857,0001524
Trade Date <05-JAN-2015>,Settlement Type <N>,Settlement No <2015003>,Settlement Date <07-JAN-2015>
Record Type,Sr No,Name of Security,Quantity Traded,Deliverable Quantity(gross across client level),% of Deliverable Quantity to Traded Quantity
20,1,20MICRONS,EQ,32024,7410,23.14
20,1270,SRTRANSFIN,EQ,542854,333552,61.44
20,1271,SRTRANSFIN,NF,95,95,100.00
20,1281,SRTRANSFIN,Y9,631,590,93.50
"""

MTO_2026 = """Security Wise Delivery Position - Compulsory Rolling Settlement
10,MTO,20112026,2432401686,0003520
Trade Date <20-NOV-2026>,Settlement Type <N>
Record Type,Sr No,Name of Security,Quantity Traded,Deliverable Quantity(gross across client level),% of Deliverable Quantity to Traded Quantity
20,1,0SCL27,YW,25,25,100.00
20,2600,RELIANCE,EQ,13167730,6456311,49.03
20,2915,SHRIRAMFIN,EQ,4785257,2513771,52.53
20,3001,ZZBE,BE,1000,1000,100.00
20,3002,ZZBE,BZ,500,10,2.00
20,3003,BADQTY,EQ,100,150,150.00
20,3004,BADPCT,EQ,1000,500,10.00
20,3005,NOTNUM,EQ,abc,1,1.00
"""

NAMES_2026 = {
    n: f"NSE:{n}" for n in ("RELIANCE", "SHRIRAMFIN", "ZZBE", "BADQTY", "BADPCT", "NOTNUM")
}
D26 = date(2026, 11, 20)

OI = """""Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Nov 20, 2026"",,,,,,,,,,,,,,
Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short       ,Option Index Call Long,Option Index Put Long,Option Index Call Short,Option Index Put Short,Option Stock Call Long,Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,Total Long Contracts      ,Total Short Contracts
Client,155681,114419,3127092,371549,3072591,4103061,3106912,4591567,1984112,1146937,1199379,1194530,13589474,10578356
DII,52405,35254,460150,4352471,6837,99777,2290,1700,5922,43360,483207,29494,668451,4904416
FII,75864,146318,3673303,2671683,882959,1425970,992566,1041238,323523,422818,439422,345424,6804437,5636651
Pro,53026,40985,742436,607278,1179262,1515473,1039881,1509777,1488787,1205303,1680336,1248970,6184287,6127227
TOTAL,336976,336976,8002981,8002981,5141649,7144281,5141649,7144281,3802344,2818418,3802344,2818418,27246649,27246649
"""

VOL_2015 = """"Participant wise Trading Volume (no. of contracts) in Equity Derivatives as on Jan 05, 2015",,,,,,,,,,,,,,
Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short,Option Index Call Long,Option Index Put Long,Option Index Call Short,Option Index Put Short,Option Stock Call Long,Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,Total Long Contracts,Total Short Contracts
Client,290814,253340,399112,404158,797749,897288,794329,884053,92893,37789,86236,35824,2515645,2457940
DII,0,8322,4672,3256,0,322,16,0,54,0,0,0,5048,11594
FII,27240,54767,62469,61923,243319,292084,224234,307411,23929,18910,23675,18863,667951,690873
Pro,141931,143556,196645,193561,1371427,1428484,1393916,1426714,73774,35387,80739,37399,3247648,3275885
TOTAL,459985,459985,662898,662898,2412495,2618178,2412495,2618178,190650,92086,190650,92086,6436292,6436292
"""


def test_urls_follow_the_archive_names() -> None:
    assert mto_url("https://h/mto/", D26) == "https://h/mto/MTO_20112026.DAT"
    assert participant_url("https://h/n", "participant_oi", D26).endswith(
        "/fao_participant_oi_20112026.csv"
    )
    assert participant_url("https://h/n", "participant_vol", D26).endswith(
        "/fao_participant_vol_20112026.csv"
    )


def test_mto_keeps_universe_shares_with_exact_basis_points() -> None:
    p = parse_mto(MTO_2026, D26, NAMES_2026)
    got = {r.symbol: (r.series, r.traded_qty, r.deliverable_qty, r.deliv_bps) for r in p.rows}
    assert got["NSE:RELIANCE"] == ("EQ", 13167730, 6456311, 4903)
    assert got["NSE:SHRIRAMFIN"] == ("EQ", 4785257, 2513771, 5253)
    assert got["NSE:ZZBE"] == ("BE", 1000, 1000, 10000)  # BE preferred over BZ, one row
    assert "NSE:0SCL27" not in got  # a bond (series YW) and not in the universe
    # deliverable > traded; printed % disagrees with the quantities; not a number
    assert p.rejected == 3
    assert {"NSE:BADQTY", "NSE:BADPCT", "NSE:NOTNUM"}.isdisjoint(got)


def test_mto_maps_the_old_name_and_skips_that_companys_bonds() -> None:
    p = parse_mto(MTO_2015, date(2015, 1, 5), {"SRTRANSFIN": "NSE:SHRIRAMFIN"})
    [row] = p.rows
    assert (row.symbol, row.source_symbol, row.series, row.deliv_bps) == (
        "NSE:SHRIRAMFIN",
        "SRTRANSFIN",
        "EQ",
        6144,
    )


def test_mto_for_another_day_is_refused_as_mislabelled() -> None:
    with pytest.raises(NseArchiveMislabeled):
        parse_mto(MTO_2026, date(2026, 11, 19), NAMES_2026)
    with pytest.raises(NseArchiveError, match="header"):
        parse_mto("Security Wise Delivery Position\n20,1,X,EQ,1,1,100.00\n", D26, {})


def test_participants_parse_four_rows_that_add_up() -> None:
    vol = parse_participants(VOL_2015, date(2015, 1, 5), "volume")
    assert [r.client_type for r in vol.rows] == ["Client", "DII", "FII", "Pro"]
    assert vol.total_gap == 0  # volume files add up exactly
    assert vol.rows[1].values[0] == 0  # DII index futures long that day: a real zero


def test_nse_oi_rounding_gap_is_accepted_within_tolerance_and_recorded() -> None:
    # the REAL 2026-11-20 file: participants' puts-short sum to 7,144,282, NSE's TOTAL says
    # 7,144,281 — off by one contract
    with pytest.raises(NseArchiveError, match="off by 1"):
        parse_participants(OI, D26, "oi", tolerance=0)
    oi = parse_participants(OI, D26, "oi", tolerance=2)
    assert oi.total_gap == 1
    fii = oi.rows[2]
    assert dict(zip(FIELDS, fii.values, strict=True))["fut_idx_short"] == 146318  # as given


def test_participant_file_must_be_the_right_kind_and_day() -> None:
    with pytest.raises(NseArchiveError, match="trading volume"):
        parse_participants(OI, D26, "volume", tolerance=2)
    with pytest.raises(NseArchiveMislabeled):
        parse_participants(OI, date(2026, 11, 19), "oi", tolerance=2)


def test_participant_totals_that_dont_add_up_fail_the_whole_file() -> None:
    bad_total = VOL_2015.replace("TOTAL,459985", "TOTAL,459995")  # off by 10
    with pytest.raises(NseArchiveError, match="TOTAL"):
        parse_participants(bad_total, date(2015, 1, 5), "volume", tolerance=2)
    bad_row = VOL_2015.replace("FII,27240,", "FII,27250,")  # its own total now off by 10
    with pytest.raises(NseArchiveError, match="add up"):
        parse_participants(bad_row, date(2015, 1, 5), "volume", tolerance=2)


def test_participant_file_with_changed_columns_or_missing_rows_fails() -> None:
    with pytest.raises(NseArchiveError, match="columns"):
        parse_participants(OI.replace("Future Index Long", "Fut Idx Long"), D26, "oi", 2)
    without_pro = "\n".join(ln for ln in OI.splitlines() if not ln.startswith("Pro,"))
    with pytest.raises(NseArchiveError):
        parse_participants(without_pro, D26, "oi", 2)


# --- deals, announcements, board meetings -------------------------------------------------------

BULK = """Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price,Remarks
20-NOV-2026,BLSPOLY,BLS Polymers Limited,ALPHAGREP SECURITIES PRIVATE LIMITED,BUY,414422,273.30,-
20-NOV-2026,BLSPOLY,BLS Polymers Limited,QE SECURITIES LLP,SELL,1071662,272.555,Note
20-NOV-2026,BADROW,Bad Limited,SOMEONE,HOLD,10,1.00,-
"""
BLOCK_EMPTY = "Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price\n"

AN_2026 = """COMPANY NAME    SYMBOL    : ANNOUNCEMENTS
Firstsource Solutions Limited FSL : Press Release FSL : Firstsource Solutions Limited has informed the Exchange regarding a press release
Ashoka Buildcon Limited ASHOKA : General Updates ASHOKA : Ashoka Buildcon Limited has informed the Exchange about Updates
"""
AN_2015 = """COMPANY NAME    SYMBOL    : ANNOUNCEMENTS
The Federal Bank  Limited    FEDERALBNK : Federal Bank has informed the Exchange regarding the outcome of the board meeting.
UTI MF - UTI - FTIF - SRXVIII Pl IV - RP-ADPO    URAPXVIII4 : Declaration of NAV
"""
BM_2015 = """COMPANY NAME    SYMBOL     : BM DATE    : BM PURPOSE
The Federal Bank  Limited    FEDERALBNK : 15-Jan-2015 : Results Inter-alia, to consider and take on record the Bank's Un-audited financial results
Satin Creditcare Network Limited SATIN : 25-NOV-2026 : Fund Raising Working Committee Meeting
Garbage line without separators
"""


def test_deals_parse_one_session_and_flag_rounded_prices() -> None:
    from app.data.nse_reference import parse_deals

    f = parse_deals(BULK, "bulk")
    assert f.trade_date == date(2026, 11, 20)
    assert [(r.client, r.side, r.quantity, r.price_paisa, r.price_exact) for r in f.rows] == [
        ("ALPHAGREP SECURITIES PRIVATE LIMITED", "BUY", 414422, 27330, True),
        ("QE SECURITIES LLP", "SELL", 1071662, 27256, False),  # 272.555 → 27256 paisa, flagged
    ]
    assert f.rows[1].remarks == "Note"
    assert f.rejected == 1  # 'HOLD' is not a side
    empty = parse_deals(BLOCK_EMPTY, "block")
    assert (empty.trade_date, empty.rows) == (None, [])


def test_deals_spanning_days_or_with_changed_columns_fail() -> None:
    from app.data.nse_reference import parse_deals

    two_days = BULK + "21-NOV-2026,X,X Ltd,Y,BUY,1,1.00,-\n"
    with pytest.raises(NseArchiveError, match="several dates"):
        parse_deals(two_days, "bulk")
    with pytest.raises(NseArchiveError, match="columns"):
        parse_deals(BULK.replace("Client Name", "Client"), "bulk")


def test_announcements_both_formats_universe_only() -> None:
    from app.data.nse_reference import parse_announcements

    new = parse_announcements(AN_2026, date(2026, 11, 20), {"FSL": "NSE:FSL"})
    [a] = new.rows
    assert (a.symbol, a.category) == ("NSE:FSL", "Press Release")
    assert a.body.startswith("Firstsource Solutions Limited has informed")
    old = parse_announcements(AN_2015, date(2015, 1, 5), {"FEDERALBNK": "NSE:FEDERALBNK"})
    [b] = old.rows
    assert (b.symbol, b.category) == ("NSE:FEDERALBNK", None)  # the 2015 format has no category


def test_board_meetings_mark_results_and_reject_bad_lines() -> None:
    from app.data.nse_reference import parse_board_meetings

    names = {"FEDERALBNK": "NSE:FEDERALBNK", "SATIN": "NSE:SATIN"}
    p = parse_board_meetings(BM_2015, date(2015, 1, 5), names)
    assert [(r.symbol, r.meeting_date, r.is_results) for r in p.rows] == [
        ("NSE:FEDERALBNK", date(2015, 1, 15), True),
        ("NSE:SATIN", date(2026, 11, 25), False),
    ]
    assert p.rejected == 1
