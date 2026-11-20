"""NSE archive parsing + fetching. Samples are real lines from NSE PR zips (2015/2022/2025)."""

# ruff: noqa: E501 — the CSV fixtures are verbatim NSE lines and must not be re-wrapped

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime

import httpx
import pytest

from app.broker.kite.client import RateLimiter
from app.data.nse_archive import (
    NseArchiveClient,
    NseArchiveError,
    NseArchiveRefused,
    archive_url,
    parse_bc,
    parse_nse_date,
    parse_pd,
    parse_purpose,
    read_members,
)
from tests.conftest import SettingsFactory
from tests.fixtures.clock import FrozenClock

UNIVERSE = {"NSE:TATASTEEL", "NSE:RECLTD", "NSE:RELIANCE", "NSE:HCLTECH", "NSE:WIPRO"}

BC_2022 = """SERIES,SYMBOL,SECURITY,RECORD_DT,BC_STRT_DT,BC_END_DT,EX_DT,ND_STRT_DT,ND_END_DT,PURPOSE
17,SRTRANSFIN,Sec Red NCD 9.5% Sr.V,06/08/2022, , ,04/08/2022, , ,INTEREST PAYMENT
BE,TATASTEEL,Tata Steel Limited,29/07/2022, , ,28/07/2022, , ,FVSPLT FRM RS 10 TO RE 1
EQ,TATASTEEL,Tata Steel Limited,29/07/2022, , ,28/07/2022, , ,FVSPLT FRM RS 10 TO RE 1
EQ,RECLTD,Rural Electrification Cor,18/08/2022, , ,17/08/2022, , ,BONUS 1:3
EQ,WIPRO,Wipro Ltd, , , ,not-a-date, , ,INTERIM DIVIDEND
"""

BC_2025 = """SERIES,SYMBOL,SECURITY,RECORD_DT,BC_STRT_DT,BC_END_DT,EX_DT,ND_STRT_DT,ND_END_DT,PURPOSE
EQ,HCLTECH,HCL Technologies Ltd,2025-10-17, , ,2025-10-17, , ,INTDIV - RS 12 PER SH
EQ,ABINFRA,AB Infra,2025-10-17, , ,2025-10-17, , ,FVSPLT FRM RS 10 TO RE 1
"""

PD = """MKT,SERIES,SYMBOL,SECURITY,PREV_CL_PR,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,CLOSE_PRICE,NET_TRDVAL,NET_TRDQTY,IND_SEC,CORP_IND,TRADES,HI_52_WK,LO_52_WK
Y, , ,Nifty 50,     16641.80,     16774.85,     16947.65,     16746.25,     16929.60,579076950551.30,2285168366,Y, ,18706761,     18604.45,     15183.40
N,EQ,RELIANCE,RELIANCE INDUSTRIES LTD,      2419.20,      2436.00,      2467.95,      2421.00,      2457.10,              14110146776.35,5766936,Y, ,207490,      2856.15,      2016.25
N,EQ,TATASTEEL,TATA STEEL LIMITED,       959.40,        98.10,       102.00,        97.15,       100.35,              13644336927.10,137156107,Y, ,628262,      1534.50,        97.15
N,T0,TATASTEEL,TATA STEEL LIMITED,       173.24,       174.00,       174.00,       174.00,       174.00,                      174.00,1,N, ,1,       174.00,       161.00
N,EQ,HCLTECH,HCL TECH,       1000.00,      1000.00,       990.00,       995.00,       998.00,      1.0,10,Y, ,1,       1.0,        1.0
N,EQ,WIPRO,WIPRO,       400.00,      401.00,       405.00,       399.00,       abc,      1.0,10,Y, ,1,       1.0,        1.0
N,EQ,INFY,INFOSYS,       1400.00,      1401.00,       1405.00,       1399.00,       1400.00,      1.0,10,Y, ,1,       1.0,        1.0
"""


@pytest.mark.parametrize(
    ("purpose", "expected"),
    [
        ("FVSPLT FRM RS 10 TO RE 1", {"kinds": ("split",), "split": (1000, 100)}),
        ("FV SPLT FRM RS 10 TO RS 2", {"kinds": ("split",), "split": (1000, 200)}),
        (
            "FACE VALUE SPLIT (SUB-DIVISION) - FROM RS 10/- PER SHARE TO RE 1/- PER SHARE",
            {"kinds": ("split",), "split": (1000, 100)},
        ),
        ("BONUS 1:3", {"kinds": ("bonus",), "bonus": (1, 3)}),
        ("AGM/DIV-RS 44/- PER SHARE", {"kinds": ("dividend",), "div": 4400}),
        ("INTDIV - RE 0.50 PER SH", {"kinds": ("dividend",), "div": 50}),
        ("INT DIV-RS 2 PER SHARE", {"kinds": ("dividend",), "div": 200}),
        ("2ND INTERIM DIVIDEND", {"kinds": ("dividend",), "div": None}),  # amount unknown
        (
            "DIV/FV SPLT RS 10 TO RS 5",
            {"kinds": ("split", "dividend"), "split": (1000, 500), "div": None},
        ),
        (
            "INTDIV - RS 5 PER SH / SPL DIV - RS 3 PER SH",
            {"kinds": ("dividend",), "div": 800},
        ),
        (
            "DIV - RS 2 PER SH / BONUS 1:1",
            {"kinds": ("bonus", "dividend"), "bonus": (1, 1), "div": 200},
        ),
        (
            "BONUS1:1/FV SPL-RS10TORS5",  # TECHM 2015: no spaces around TO
            {"kinds": ("split", "bonus"), "split": (1000, 500), "bonus": (1, 1)},
        ),
        ("DEMERGER", {"kinds": ("other",)}),
        ("SCHEME OF ARRANGEMENT", {"kinds": ("other",)}),
        ("RIGHTS 1:15 @ PREMIUM RS 1247", {"kinds": ("other",)}),
        ("AGM", {"kinds": ("other",)}),  # nothing price-relevant: kept as 'other'
    ],
)
def test_purpose_components(purpose: str, expected: dict[str, object]) -> None:
    p = parse_purpose(purpose)
    assert p["kinds"] == expected["kinds"]
    if "split" in expected:
        assert (p["split_from_paisa"], p["split_to_paisa"]) == expected["split"]
    if "bonus" in expected:
        assert (p["bonus_new"], p["bonus_held"]) == expected["bonus"]
    if "div" in expected:
        assert p["dividend_paisa"] == expected["div"]
    else:
        assert "dividend_paisa" not in p


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("29/07/2022", date(2022, 7, 29)),
        ("2025-10-17", date(2025, 10, 17)),
        ("17-Oct-2025", date(2025, 10, 17)),
        (" ", None),
        ("-", None),
    ],
)
def test_nse_dates_in_every_format_seen(text: str, expected: date | None) -> None:
    assert parse_nse_date(text) == expected


def test_unparseable_date_raises() -> None:
    with pytest.raises(ValueError, match="unparseable"):
        parse_nse_date("31/31/2022")


def test_bc_keeps_universe_equity_actions_rejects_bad_dates() -> None:
    p = parse_bc(BC_2022, UNIVERSE)
    assert [(r.symbol, r.series, r.ex_date, r.kinds) for r in p.rows] == [
        ("NSE:TATASTEEL", "BE", date(2022, 7, 28), ("split",)),
        ("NSE:TATASTEEL", "EQ", date(2022, 7, 28), ("split",)),
        ("NSE:RECLTD", "EQ", date(2022, 8, 17), ("bonus",)),
    ]
    assert p.rejected == 1  # WIPRO: EX_DT 'not-a-date' — never guessed
    tata = p.rows[1]
    assert (tata.split_from_paisa, tata.split_to_paisa, tata.record_date) == (
        1000,
        100,
        date(2022, 7, 29),
    )


def test_bc_iso_dates_2025_format() -> None:
    p = parse_bc(BC_2025, UNIVERSE)
    assert [(r.symbol, r.ex_date, r.dividend_paisa) for r in p.rows] == [
        ("NSE:HCLTECH", date(2025, 10, 17), 1200)
    ]  # ABINFRA is not in the universe


def test_pd_official_prices_exact_paisa() -> None:
    p = parse_pd(PD, date(2022, 7, 28), UNIVERSE)
    by = {r.symbol: r for r in p.rows}
    assert set(by) == {"NSE:RELIANCE", "NSE:TATASTEEL"}  # INFY not in universe; index row skipped
    t = by["NSE:TATASTEEL"]  # EQ row only — the T0 row is ignored
    assert (t.prev_close, t.open, t.high, t.low, t.close, t.volume) == (
        95940,
        9810,
        10200,
        9715,
        10035,
        137156107,
    )
    # HCLTECH: high 990 < open 1000 (bad order); WIPRO: close 'abc'
    assert p.rejected == 2


def test_pd_identical_repeat_is_kept_once() -> None:
    # NSE repeated whole rows verbatim on 2025-03-28 (BAJAJHFL, CGPOWER, HYUNDAI, …)
    twice = PD + PD.splitlines()[2] + "\n"
    p = parse_pd(twice, date(2022, 7, 28), UNIVERSE)
    assert [r.symbol for r in p.rows].count("NSE:RELIANCE") == 1
    assert p.rejected == 2  # only the two genuinely bad rows


def test_pd_conflicting_repeat_is_rejected_entirely() -> None:
    other = PD.splitlines()[2].replace("2457.10", "2457.20")
    p = parse_pd(PD + other + "\n", date(2022, 7, 28), UNIVERSE)
    assert "NSE:RELIANCE" not in {r.symbol for r in p.rows}
    assert p.rejected == 3


def _zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in members.items():
            z.writestr(name, text)
    return buf.getvalue()


def test_members_found_whatever_the_name_case() -> None:
    bc, pd = read_members(_zip({"bc16102025.csv": "B", "pd16102025.csv": "P", "x.txt": ""}), 10**6)
    assert (bc, pd) == ("B", "P")
    bc, pd = read_members(_zip({"Bc280722.csv": "B2", "Pd280722.csv": "P2"}), 10**6)
    assert (bc, pd) == ("B2", "P2")


@pytest.mark.parametrize(
    ("blob", "match"),
    [
        (b"not a zip", "not a zip"),
        (_zip({"Pd280722.csv": "P"}), "lacks member"),
        (_zip({"Bc280722.csv": "B" * 5000, "Pd280722.csv": "P"}), "too large"),
    ],
)
def test_bad_zips_fail_closed(blob: bytes, match: str) -> None:
    with pytest.raises(NseArchiveError, match=match):
        read_members(blob, 1000)


def test_archive_url() -> None:
    assert archive_url("https://h/pr/", date(2022, 7, 28)) == "https://h/pr/PR280722.zip"


# --- fetching ---


def _client(
    make_settings: SettingsFactory, handler: httpx.MockTransport, sleeps: list[float]
) -> tuple[NseArchiveClient, httpx.AsyncClient]:
    async def sleep(s: float) -> None:
        sleeps.append(s)

    async def limiter_sleep(_s: float) -> None:
        return None

    s = make_settings(nse_archive_retries=2, nse_archive_retry_backoff_seconds=1.0)
    http = httpx.AsyncClient(transport=handler)
    limiter = RateLimiter(1000.0, FrozenClock(datetime(2026, 1, 1, tzinfo=UTC)), limiter_sleep)
    return NseArchiveClient(http, s, limiter, sleep), http


async def test_fetch_sends_honest_user_agent_and_returns_bytes(
    make_settings: SettingsFactory,
) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=b"ZIP")

    client, http = _client(make_settings, httpx.MockTransport(handler), [])
    async with http:
        assert await client.fetch_zip(date(2022, 7, 28)) == b"ZIP"
    assert seen[0].url.path.endswith("/PR280722.zip")
    ua = seen[0].headers["user-agent"]
    assert ua.startswith("FinTrack/")
    assert "Mozilla" not in ua


async def test_missing_day_is_none_not_error(make_settings: SettingsFactory) -> None:
    client, http = _client(make_settings, httpx.MockTransport(lambda r: httpx.Response(404)), [])
    async with http:
        assert await client.fetch_zip(date(2022, 7, 28)) is None


async def test_refusal_is_never_retried(make_settings: SettingsFactory) -> None:
    calls: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(403)

    client, http = _client(make_settings, httpx.MockTransport(handler), [])
    async with http:
        with pytest.raises(NseArchiveRefused):
            await client.fetch_zip(date(2022, 7, 28))
    assert len(calls) == 1


async def test_server_errors_retry_with_backoff_then_fail(make_settings: SettingsFactory) -> None:
    sleeps: list[float] = []
    client, http = _client(
        make_settings, httpx.MockTransport(lambda r: httpx.Response(503)), sleeps
    )
    async with http:
        with pytest.raises(NseArchiveError, match="after 3 attempts: HTTP 503"):
            await client.fetch_zip(date(2022, 7, 28))
    assert sleeps == [1.0, 2.0]


async def test_oversized_zip_is_refused(make_settings: SettingsFactory) -> None:
    big = b"x" * (int(0.5 * 1024 * 1024) + 1)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=big))
    ) as http:

        async def sleep(_s: float) -> None:
            return None

        limiter = RateLimiter(1000.0, FrozenClock(datetime(2026, 1, 1, tzinfo=UTC)), sleep)
        client = NseArchiveClient(http, make_settings(nse_archive_max_zip_mb=0.5), limiter, sleep)
        with pytest.raises(NseArchiveError, match="too large"):
            await client.fetch_zip(date(2022, 7, 28))


def test_zip_holding_another_days_files_is_refused() -> None:
    # real: NSE's PR020118.zip (2018-01-02) contains Bc020119.csv / Pd020119.csv (2019-01-02)
    blob = _zip({"Bc020119.csv": "B", "Pd020119.csv": "P"})
    with pytest.raises(NseArchiveError, match="holds bc file dated 2019-01-02"):
        read_members(blob, 10**6, expected=date(2018, 1, 2))
    assert read_members(blob, 10**6, expected=date(2019, 1, 2)) == ("B", "P")
    long_names = _zip({"bc16102025.csv": "B", "pd16102025.csv": "P"})
    assert read_members(long_names, 10**6, expected=date(2025, 10, 16)) == ("B", "P")
