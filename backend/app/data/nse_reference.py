"""Phase 1e step 1 parsers (DEC-1E-SOURCES): NSE delivery position + participant positions.

Both files come from NSE's static archive (the same honest client and refusal rules as the
PR zips). Each file states its own date; a file whose date differs from the one requested is
refused as mislabelled — never stored under the wrong day. Anything that does not parse
exactly is rejected (a row) or fails the whole file (a header, a total) — never guessed.

  MTO_ddmmyyyy.DAT   security-wise delivery: one "20,…" line per security and series, all
                     instruments (shares, bonds, …); only universe shares (EQ/BE/BZ) are kept.
  fao_participant_{oi|vol}_ddmmyyyy.csv
                     contracts held / traded by Client, DII, FII and Pro, plus a TOTAL row
                     that must equal their sum; each row's total long/short must equal its
                     legs.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.core.money import rupees_to_paisa
from app.data.nse_archive import PD_SERIES, NseArchiveError, NseArchiveMislabeled, Parsed

MTO_DATASET = "mto"
PARTICIPANT_DATASETS = {"participant_oi": "oi", "participant_vol": "volume"}
PR_TEXT_DATASET = "pr_text"
MARKET_CAP_DATASET = "market_cap"
# NSE/CMTR/60423 introduced the member on this date.
MARKET_CAP_START = date(2024, 2, 1)
PR_DATASETS = (PR_TEXT_DATASET, MARKET_CAP_DATASET)
# per-date datasets (history back to NSE_ARCHIVE_START); deals are forward-only (latest session)
DATED_DATASETS = (MTO_DATASET, *PARTICIPANT_DATASETS, *PR_DATASETS)
DATASETS = (*DATED_DATASETS, "bulk_deals", "block_deals")


def mto_url(base: str, d: date) -> str:
    return f"{base.rstrip('/')}/MTO_{d:%d%m%Y}.DAT"


def participant_url(base: str, dataset: str, d: date) -> str:
    part = {"participant_oi": "oi", "participant_vol": "vol"}[dataset]
    return f"{base.rstrip('/')}/fao_participant_{part}_{d:%d%m%Y}.csv"


# --- delivery position (MTO) ---------------------------------------------------------------


@dataclass(frozen=True)
class DeliveryRow:
    symbol: str  # NSE:SYMBOL (our current name)
    trade_date: date
    series: str
    traded_qty: int
    deliverable_qty: int
    deliv_bps: int
    source_symbol: str = ""  # NSE's name that day


_MTO_HEAD = re.compile(r"^10,MTO,(\d{8}),")


def _bps(deliverable: int, traded: int) -> int:
    """Integer basis points, half up — exact, no floats."""
    return (deliverable * 10_000 * 2 + traded) // (traded * 2)


def parse_mto(text: str, d: date, names: Mapping[str, str]) -> Parsed[DeliveryRow]:
    """Universe shares from one MTO file. `names` maps NSE's name ON `d` → our symbol.
    Rows: 20,<sr>,<name>,<series>,<traded>,<deliverable>,<pct>. A share series row that
    doesn't parse, has deliverable > traded, or disagrees with NSE's printed % by more than
    0.01 point is rejected. One stored row per symbol: EQ, else BE, else BZ."""
    lines = text.splitlines()
    stated = next((m.group(1) for ln in lines[:5] if (m := _MTO_HEAD.match(ln.strip()))), None)
    if stated is None:
        raise NseArchiveError("MTO file lacks its '10,MTO,<date>' header")
    try:
        got = datetime.strptime(stated, "%d%m%Y").date()  # noqa: DTZ007 — a calendar date
    except ValueError as exc:
        raise NseArchiveError(f"MTO header date unreadable: {stated!r}") from exc
    if got != d:
        raise NseArchiveMislabeled(f"MTO file for {d} is dated {got}")
    best: dict[str, DeliveryRow] = {}
    rejected = 0
    for ln in lines:
        f = [x.strip() for x in ln.split(",")]
        if len(f) < 7 or f[0] != "20":
            continue
        name, series = f[2], f[3]
        symbol = names.get(name)
        if symbol is None or series not in PD_SERIES:
            continue  # not a universe share (bonds, other companies)
        try:
            traded, deliverable = int(f[4]), int(f[5])
            printed_bps = int((Decimal(f[6]) * 100).to_integral_value(ROUND_HALF_UP))
        except (ValueError, InvalidOperation):
            rejected += 1
            continue
        if traded <= 0 or deliverable < 0 or deliverable > traded:
            rejected += 1
            continue
        bps = _bps(deliverable, traded)
        if abs(bps - printed_bps) > 1:
            rejected += 1
            continue
        row = DeliveryRow(symbol, d, series, traded, deliverable, bps, name)
        prev = best.get(symbol)
        if prev is None or PD_SERIES.index(series) < PD_SERIES.index(prev.series):
            best[symbol] = row
    return Parsed(sorted(best.values(), key=lambda r: r.symbol), rejected)


# --- participant-wise OI / volume ------------------------------------------------------------

PARTICIPANT_COLUMNS = (
    "Future Index Long",
    "Future Index Short",
    "Future Stock Long",
    "Future Stock Short",
    "Option Index Call Long",
    "Option Index Put Long",
    "Option Index Call Short",
    "Option Index Put Short",
    "Option Stock Call Long",
    "Option Stock Put Long",
    "Option Stock Call Short",
    "Option Stock Put Short",
    "Total Long Contracts",
    "Total Short Contracts",
)
FIELDS = (
    "fut_idx_long",
    "fut_idx_short",
    "fut_stk_long",
    "fut_stk_short",
    "opt_idx_call_long",
    "opt_idx_put_long",
    "opt_idx_call_short",
    "opt_idx_put_short",
    "opt_stk_call_long",
    "opt_stk_put_long",
    "opt_stk_call_short",
    "opt_stk_put_short",
    "total_long",
    "total_short",
)
CLIENT_TYPES = ("Client", "DII", "FII", "Pro")
_LONG = (0, 2, 4, 5, 8, 9)  # indices into FIELDS that make up total_long
_SHORT = (1, 3, 6, 7, 10, 11)
_TITLE = {"oi": "open interest", "volume": "trading volume"}
_AS_ON = re.compile(r"as on\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,\s*|\s+)(\d{4})", re.IGNORECASE)


@dataclass(frozen=True)
class ParticipantFile:
    rows: list[ParticipantRow]
    # largest |participants' sum - NSE's own total| over every column and row check, in
    # contracts. NSE's OI files are often off by 1-2 (measured 2026-11-21: 16 of 40 sampled OI
    # days; volume files exact). Stored as given; the gap is recorded, never hidden.
    total_gap: int


@dataclass(frozen=True)
class ParticipantRow:
    trade_date: date
    kind: str  # 'oi' | 'volume'
    client_type: str
    values: tuple[int, ...]  # in FIELDS order


def parse_participants(text: str, d: date, kind: str, tolerance: int = 0) -> ParticipantFile:
    """The four participant rows, or an error for the whole file (never a partial day).
    Totals may differ from the legs / the TOTAL row by at most `tolerance` contracts."""
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise NseArchiveError("participant file is empty")
    # the title line is read raw: NSE sometimes double-quotes it (""…""), which makes a CSV
    # reader split it at the comma inside "Nov 20, 2026"
    first = next(ln for ln in text.splitlines() if ln.strip())
    title = first.strip().rstrip(",").strip().strip('"')
    if _TITLE[kind] not in title.lower():
        raise NseArchiveError(f"participant file is not {_TITLE[kind]}: {title[:80]!r}")
    m = _AS_ON.search(title)
    if m is None:
        raise NseArchiveError(f"participant file title has no date: {title[:80]!r}")
    try:
        got = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y").date()  # noqa: DTZ007
    except ValueError as exc:
        raise NseArchiveError(f"participant file date unreadable: {m.group(0)!r}") from exc
    if got != d:
        raise NseArchiveMislabeled(f"participant {kind} file for {d} is dated {got}")
    header = tuple(c.strip() for c in rows[1] if c.strip())
    if header[:1] != ("Client Type",) or header[1:] != PARTICIPANT_COLUMNS:
        raise NseArchiveError(f"participant file columns changed: {header!r}")
    found: dict[str, tuple[int, ...]] = {}
    worst = 0
    for r in rows[2:]:
        cells = [c.strip() for c in r]
        while cells and not cells[-1]:
            cells.pop()
        if not cells:
            continue
        who = cells[0]
        if who not in (*CLIENT_TYPES, "TOTAL") or who in found:
            raise NseArchiveError(f"unexpected participant row {who!r}")
        if len(cells) != 1 + len(FIELDS):
            raise NseArchiveError(f"participant row {who!r} has {len(cells) - 1} values")
        try:
            vals = tuple(int(c) for c in cells[1:])
        except ValueError as exc:
            raise NseArchiveError(f"participant row {who!r} is not whole numbers") from exc
        if min(vals) < 0:
            raise NseArchiveError(f"participant row {who!r} has a negative count")
        gap = max(
            abs(vals[12] - sum(vals[i] for i in _LONG)),
            abs(vals[13] - sum(vals[i] for i in _SHORT)),
        )
        if gap > tolerance:
            raise NseArchiveError(f"participant row {who!r}: totals don't add up (off by {gap})")
        worst = max(worst, gap)
        found[who] = vals
    missing = [c for c in (*CLIENT_TYPES, "TOTAL") if c not in found]
    if missing:
        raise NseArchiveError(f"participant file lacks rows: {missing}")
    sums = tuple(sum(found[c][i] for c in CLIENT_TYPES) for i in range(len(FIELDS)))
    gap = max(abs(a - b) for a, b in zip(sums, found["TOTAL"], strict=True))
    if gap > tolerance:
        raise NseArchiveError(f"participant rows don't add up to the file's TOTAL (off by {gap})")
    rows_out = [ParticipantRow(d, kind, c, found[c]) for c in CLIENT_TYPES]
    return ParticipantFile(rows_out, max(worst, gap))


# --- bulk / block deals (content/equities/bulk.csv, block.csv) -------------------------------

DEAL_DATASETS = {"bulk_deals": "bulk", "block_deals": "block"}
_DEAL_HEADER = (
    "Date",
    "Symbol",
    "Security Name",
    "Client Name",
    "Buy/Sell",
    "Quantity Traded",
    "Trade Price / Wght. Avg. Price",
)


def deals_url(base: str, dataset: str) -> str:
    return f"{base.rstrip('/')}/{DEAL_DATASETS[dataset]}.csv"


@dataclass(frozen=True)
class DealRow:
    trade_date: date
    kind: str
    symbol: str  # NSE:SYMBOL as printed that day
    security_name: str
    client: str
    side: str
    quantity: int
    price_paisa: int
    price_exact: bool
    remarks: str | None


@dataclass(frozen=True)
class DealFile:
    trade_date: date | None  # None: the file has no rows (no deals published)
    rows: list[DealRow]
    rejected: int


def parse_deals(text: str, kind: str) -> DealFile:
    """One deals file. All rows must carry the same date (the file covers one session);
    a row that doesn't parse is rejected. A header-only file has no date."""
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        raise NseArchiveError(f"{kind} deals file is empty")
    header = tuple(c.strip() for c in rows[0])
    if header[: len(_DEAL_HEADER)] != _DEAL_HEADER:
        raise NseArchiveError(f"{kind} deals columns changed: {header!r}")
    has_remarks = len(header) > len(_DEAL_HEADER) and header[len(_DEAL_HEADER)] == "Remarks"
    out: list[DealRow] = []
    dates: set[date] = set()
    rejected = 0
    for r in rows[1:]:
        c = [x.strip() for x in r]
        try:
            d = datetime.strptime(c[0], "%d-%b-%Y").date()  # noqa: DTZ007 — a calendar date
            side = c[4].upper()
            qty = int(c[5])
            price = Decimal(c[6])
        except (ValueError, IndexError, InvalidOperation):
            rejected += 1
            continue
        if side not in ("BUY", "SELL") or qty <= 0 or price <= 0 or not c[1] or not c[3]:
            rejected += 1
            continue
        paisa = (price * 100).to_integral_value(ROUND_HALF_UP)
        remarks = c[7] if has_remarks and len(c) > 7 and c[7] not in ("", "-") else None
        dates.add(d)
        out.append(
            DealRow(
                d,
                kind,
                f"NSE:{c[1]}",
                c[2],
                c[3],
                side,
                qty,
                int(paisa),
                paisa == price * 100,
                remarks,
            )
        )
    if len(dates) > 1:
        raise NseArchiveError(f"{kind} deals file spans several dates: {sorted(dates)}")
    return DealFile(next(iter(dates), None), out, rejected)


# --- PR zip text members: announcements (an*.txt) and board meetings (bm*.txt) ---------------

_AN_NAME = re.compile(r"^an\d{6}(\d{2})?\.txt$", re.IGNORECASE)
_BM_NAME = re.compile(r"^bm\d{6}(\d{2})?\.txt$", re.IGNORECASE)
_RESULTS = re.compile(r"\bresults?\b", re.IGNORECASE)


@dataclass(frozen=True)
class AnnouncementRow:
    symbol: str
    announced_on: date
    category: str | None
    body: str
    source_symbol: str


@dataclass(frozen=True)
class BoardMeetingRow:
    symbol: str
    meeting_date: date
    purpose: str
    is_results: bool
    first_seen: date
    source_symbol: str


def _split_company_symbol(left: str) -> tuple[str, str] | None:
    """'Ashoka Buildcon Limited ASHOKA' -> ('Ashoka Buildcon Limited', 'ASHOKA')."""
    parts = left.rstrip().rsplit(None, 1)
    if len(parts) != 2 or not re.fullmatch(r"[A-Z0-9&\-]+", parts[1]):
        return None
    return parts[0].strip(), parts[1]


def parse_announcements(text: str, d: date, names: Mapping[str, str]) -> Parsed[AnnouncementRow]:
    """Universe shares only. Lines: '<company> <SYMBOL> : <text>'; since ~2020 the text is
    '<category> <SYMBOL> : <body>' (the category is kept separately when present)."""
    out: list[AnnouncementRow] = []
    rejected = 0
    for ln in text.splitlines()[1:]:  # the first line is the column header
        if not ln.strip():
            continue
        left, sep, rest = ln.partition(" : ")
        cs = _split_company_symbol(left) if sep else None
        if cs is None:
            rejected += 1
            continue
        _, nse_name = cs
        symbol = names.get(nse_name)
        if symbol is None:
            continue
        category: str | None = None
        body = rest.strip()
        marker = f" {nse_name} : "
        if marker in rest:
            category, _, body = rest.partition(marker)
            category, body = category.strip() or None, body.strip()
        if not body:
            rejected += 1
            continue
        out.append(AnnouncementRow(symbol, d, category, body[:2000], nse_name))
    return Parsed(out, rejected)


def parse_board_meetings(text: str, d: date, names: Mapping[str, str]) -> Parsed[BoardMeetingRow]:
    """Universe shares only. Lines: '<company> <SYMBOL> : <dd-Mon-yyyy> : <purpose>'."""
    out: list[BoardMeetingRow] = []
    rejected = 0
    for ln in text.splitlines()[1:]:
        if not ln.strip():
            continue
        parts = ln.split(" : ", 2)
        cs = _split_company_symbol(parts[0]) if len(parts) == 3 else None
        if cs is None:
            rejected += 1
            continue
        _, nse_name = cs
        symbol = names.get(nse_name)
        if symbol is None:
            continue
        try:
            when = datetime.strptime(parts[1].strip(), "%d-%b-%Y").date()  # noqa: DTZ007
        except ValueError:
            rejected += 1
            continue
        purpose = " ".join(parts[2].split())
        if not purpose:
            rejected += 1
            continue
        out.append(
            BoardMeetingRow(
                symbol, when, purpose[:2000], bool(_RESULTS.search(purpose)), d, nse_name
            )
        )
    return Parsed(out, rejected)


@dataclass(frozen=True)
class MarketCapRow:
    symbol: str
    series: str
    issue_size: int
    market_cap_paisa: int
    source_symbol: str


_MCAP_NAME = re.compile(r"mcap\d{8}\.csv\Z", re.IGNORECASE)
_MCAP_FIELDS = ("Trade Date", "Symbol", "Series", "Issue Size", "Market Cap(Rs.)")
_MCAP_MONEY = re.compile(r"[0-9]{1,17}(?:\.[0-9]{1,2})?\Z")
_BIGINT_MAX = 2**63 - 1


def parse_market_cap(text: str, d: date, names: Mapping[str, str]) -> Parsed[MarketCapRow]:
    """Keep NSE's reported market cap and issue size for universe share series."""
    reader = csv.reader(io.StringIO(text))
    fields = [value.strip() for value in next(reader, [])]
    if any(fields.count(field) != 1 for field in _MCAP_FIELDS):
        raise NseArchiveError("market-cap CSV has an unexpected header")
    indices = [fields.index(field) for field in _MCAP_FIELDS]
    best: dict[str, MarketCapRow] = {}
    rejected = 0
    has_dated_row = False
    for row in reader:
        try:
            stated, name, series, size_text, cap_text = (row[i].strip() for i in indices)
            got = datetime.strptime(stated, "%d %b %Y").date()  # noqa: DTZ007
        except (IndexError, ValueError):
            rejected += bool(row)
            continue
        if got != d:
            raise NseArchiveMislabeled(f"market-cap file for {d} contains data for {got}")
        has_dated_row = True
        symbol = names.get(name)
        if symbol is None or series not in PD_SERIES:
            continue
        try:
            if not _MCAP_MONEY.fullmatch(cap_text) or not re.fullmatch(r"[0-9]{1,19}", size_text):
                raise ValueError("invalid market cap or issue size")
            cap, size = rupees_to_paisa(cap_text), int(size_text)
        except ValueError:
            rejected += 1
            continue
        if not (0 < cap <= _BIGINT_MAX and 0 < size <= _BIGINT_MAX):
            rejected += 1
            continue
        existing = best.get(symbol)
        if existing is not None and existing.series == series:
            raise NseArchiveError(f"duplicate market-cap row for {symbol} ({series})")
        if existing is None or PD_SERIES.index(series) < PD_SERIES.index(existing.series):
            best[symbol] = MarketCapRow(symbol, series, size, cap, name)
    if not has_dated_row:
        raise NseArchiveError("market-cap CSV has no dated rows")
    return Parsed(sorted(best.values(), key=lambda r: r.symbol), rejected)


def read_pr_members(
    blob: bytes, max_member_bytes: int, expected: date, dataset: str
) -> dict[str, str]:
    """Selected PR members, each validated against the requested date and size cap."""
    import zipfile

    from app.data.nse_archive import _member_date

    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise NseArchiveError("not a zip file") from exc
    patterns = (
        (("mcap", _MCAP_NAME),)
        if dataset == MARKET_CAP_DATASET
        else (
            ("an", _AN_NAME),
            ("bm", _BM_NAME),
        )
    )
    texts: dict[str, str] = {}
    with zf:
        for info in zf.infolist():
            name = info.filename.rsplit("/", 1)[-1]
            for key, pattern in patterns:
                if not pattern.match(name):
                    continue
                got = _member_date(name[:-4] + ".csv")
                if got != expected:
                    raise NseArchiveMislabeled(f"zip for {expected} holds {key} file dated {got}")
                if key in texts:
                    raise NseArchiveError(f"zip contains more than one {key} member")
                if info.file_size > max_member_bytes:
                    raise NseArchiveError(f"{key} member too large: {info.file_size} bytes")
                with zf.open(info) as fh:
                    data = fh.read(max_member_bytes + 1)
                if len(data) > max_member_bytes:
                    raise NseArchiveError(f"{key} member inflates past {max_member_bytes} bytes")
                texts[key] = data.decode("utf-8-sig", errors="replace")
    return texts
