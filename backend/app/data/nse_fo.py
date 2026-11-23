"""NSE F&O bhavcopy (slice 1f): daily futures and options, 2015 onwards.

Two file formats, both a zip holding one CSV:
  old   content/historical/DERIVATIVES/<YYYY>/<MON>/fo<DD><MON><YYYY>bhav.csv.zip   (to 2024)
  UDiFF content/fo/BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip                  (2024-01 on)
Both are normalised to the same row. Prices become exact integer paisa (never floats). A row
that doesn't parse, or whose high is below its low, is rejected. An open/close outside the day's
range is NSE's own quirk in thin contracts: kept and counted. Contracts with no trades and no
open interest are skipped (counted, not rejected). Every row's date must equal the date
requested, or the whole file is refused as mislabelled — never stored under the wrong day.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.core.money import rupees_to_paisa
from app.data.nse_archive import NseArchiveError, NseArchiveMislabeled

FO_DATASET = "fo_bhav"
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
INT4_MAX = 2_147_483_647

_OLD_KIND = {"FUTIDX": "IDF", "FUTSTK": "STF", "OPTIDX": "IDO", "OPTSTK": "STO", "FUTIVX": "IVF"}
_NEW_KINDS = {"IDF", "STF", "IDO", "STO", "IVF"}


def fo_url(old_base: str, new_base: str, udiff_from: date, d: date) -> str:
    if d >= udiff_from:
        return f"{new_base.rstrip('/')}/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    mon = _MONTHS[d.month - 1]
    return f"{old_base.rstrip('/')}/{d.year}/{mon}/fo{d.day:02d}{mon}{d.year}bhav.csv.zip"


@dataclass(frozen=True)
class FoRow:
    trade_date: date
    underlying: str
    kind: str
    expiry: date
    strike_paisa: int
    opt_type: str  # FUT | CE | PE
    open: int
    high: int
    low: int
    close: int
    settle: int
    prev_close: int | None
    underlying_price: int | None
    contracts: int
    open_interest: int
    oi_change: int
    lot_size: int | None


@dataclass(frozen=True)
class FoFile:
    rows: list[FoRow]
    rejected: int
    idle: int  # listed contracts with no trades and no open interest: skipped
    odd_range: int  # traded contracts whose open/close lies outside high-low (NSE's own data): kept


def read_csv(blob: bytes, max_bytes: int) -> str:
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise NseArchiveError("not a zip file") from exc
    members = [i for i in zf.infolist() if i.filename.lower().endswith(".csv")]
    if len(members) != 1:
        raise NseArchiveError(f"expected one CSV in the zip, found {len(members)}")
    info = members[0]
    if info.file_size > max_bytes:
        raise NseArchiveError(f"F&O member too large: {info.file_size} bytes")
    with zf.open(info) as fh:
        data = fh.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise NseArchiveError(f"F&O member inflates past {max_bytes} bytes")
    return data.decode("utf-8", errors="replace")


def _paisa(text: str) -> int:
    v = rupees_to_paisa(Decimal(text.strip() or "0"))
    if v > INT4_MAX:
        raise ValueError("price beyond the stored range")
    return int(v)


def _date_old(text: str) -> date:
    return datetime.strptime(text.strip().title(), "%d-%b-%Y").date()  # noqa: DTZ007


def parse_fo(text: str, d: date) -> FoFile:
    """One day's file in either format (told apart by its header)."""
    reader = csv.DictReader(io.StringIO(text))
    header = {h.strip() for h in (reader.fieldnames or []) if h}
    if {"TradDt", "FinInstrmTp", "TckrSymb", "XpryDt", "OpnIntrst", "TtlTradgVol"} <= header:
        udiff = True
    elif {"INSTRUMENT", "SYMBOL", "EXPIRY_DT", "OPEN_INT", "CONTRACTS", "TIMESTAMP"} <= header:
        udiff = False
    else:
        raise NseArchiveError(f"F&O file columns not recognised: {sorted(header)[:8]}")
    rows: list[FoRow] = []
    rejected = idle = odd = 0
    for r in reader:
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        try:
            if udiff:
                row_date = date.fromisoformat(r["TradDt"])
                kind = r["FinInstrmTp"]
                under, exp = r["TckrSymb"], date.fromisoformat(r["XpryDt"])
                opt = r["OptnTp"] or "FUT"
                strike = _paisa(r["StrkPric"]) if r["StrkPric"] else 0
                o, h, lo, c = (_paisa(r[k]) for k in ("OpnPric", "HghPric", "LwPric", "ClsPric"))
                settle = _paisa(r["SttlmPric"])
                prev = _paisa(r["PrvsClsgPric"]) if r["PrvsClsgPric"] else None
                und = _paisa(r["UndrlygPric"]) if r["UndrlygPric"] else None
                contracts, oi, chg = (
                    int(r["TtlTradgVol"]),
                    int(r["OpnIntrst"]),
                    int(r["ChngInOpnIntrst"]),
                )
                lot = int(r["NewBrdLotQty"]) if r.get("NewBrdLotQty") else None
            else:
                row_date = _date_old(r["TIMESTAMP"])
                kind = _OLD_KIND.get(r["INSTRUMENT"], "")
                under, exp = r["SYMBOL"], _date_old(r["EXPIRY_DT"])
                opt = "FUT" if r["OPTION_TYP"] == "XX" else r["OPTION_TYP"]
                strike = _paisa(r["STRIKE_PR"])
                o, h, lo, c = (_paisa(r[k]) for k in ("OPEN", "HIGH", "LOW", "CLOSE"))
                settle = _paisa(r["SETTLE_PR"])
                prev = und = lot = None
                contracts, oi, chg = (
                    int(r["CONTRACTS"]),
                    int(r["OPEN_INT"]),
                    int(r["CHG_IN_OI"]),
                )
        except (KeyError, ValueError, InvalidOperation, TypeError):
            rejected += 1
            continue
        if row_date != d:
            raise NseArchiveMislabeled(f"F&O file for {d} holds a row dated {row_date}")
        if kind not in _NEW_KINDS or not under or opt not in ("FUT", "CE", "PE"):
            rejected += 1
            continue
        if (opt == "FUT") != (strike == 0 and kind in ("IDF", "STF", "IVF")):
            rejected += 1
            continue
        if contracts < 0 or oi < 0:
            rejected += 1
            continue
        if contracts == 0 and oi == 0:
            idle += 1
            continue
        if contracts > 0:
            if h < lo:
                rejected += 1  # impossible: the day's high below its low
                continue
            if not (lo <= min(o, c) and max(o, c) <= h):
                odd += 1  # seen in NSE's thin contracts (2015: MOTHERSUMI, SUNTV): kept, counted
        rows.append(
            FoRow(
                d,
                under,
                kind,
                exp,
                strike,
                opt,
                o,
                h,
                lo,
                c,
                settle,
                prev,
                und,
                contracts,
                oi,
                chg,
                lot,
            )
        )
    return FoFile(rows, rejected, idle, odd)
