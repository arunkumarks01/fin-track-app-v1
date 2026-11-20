"""NSE static archive: the daily PR zip's corporate actions (Bc) and official prices (Pd).

Phase 1c (DEC-B revised). The zip is fetched into memory with an honest User-Agent; only the
two CSV members are read (size-capped against zip bombs) and nothing is written to disk.
www.nseindia.com answers non-browser clients with 403 — that is a refusal, never something
to work around: a 403 here is an error, not a reason to change headers.

Formats drift over the years (seen on real files): member names `Bc090115.csv` vs
`bc16102025.csv`, dates `dd/mm/yyyy` vs `yyyy-mm-dd`, padded/blank fields. Anything that
does not parse is counted as rejected — never guessed.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
import zipfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

from app.broker.kite.client import RateLimiter
from app.core.config import Settings
from app.core.money import rupees_to_paisa

_BC_NAME = re.compile(r"^bc\d{6}(\d{2})?\.csv$", re.IGNORECASE)
_PD_NAME = re.compile(r"^pd\d{6}(\d{2})?\.csv$", re.IGNORECASE)


class NseArchiveError(RuntimeError):
    """The archive could not be read for this date (retryable or not, never guessed)."""


class NseArchiveMislabeled(NseArchiveError):
    """The zip requested for one day holds another day's files: NSE has no correct file for
    that day (an accepted gap), never stored under the wrong date."""


class NseArchiveRefused(NseArchiveError):
    """403/401: the archive refused us. Stop — never retry with different headers."""


@dataclass(frozen=True)
class ActionRow:
    symbol: str  # NSE:SYMBOL
    series: str
    ex_date: date
    record_date: date | None
    purpose: str
    kinds: tuple[str, ...]
    split_from_paisa: int | None = None
    split_to_paisa: int | None = None
    bonus_new: int | None = None
    bonus_held: int | None = None
    dividend_paisa: int | None = None
    source_symbol: str = ""


@dataclass(frozen=True)
class BhavRow:
    symbol: str  # NSE:SYMBOL
    trade_date: date
    prev_close: int
    open: int
    high: int
    low: int
    close: int
    volume: int
    series: str = "EQ"
    source_symbol: str = ""  # NSE's symbol that day (differs from ours before a rename)


@dataclass(frozen=True)
class Parsed[T]:
    rows: list[T]
    rejected: int


def archive_url(base: str, d: date) -> str:
    return f"{base.rstrip('/')}/PR{d:%d%m%y}.zip"


# --- dates / numbers ---------------------------------------------------------------------


def parse_nse_date(text: str) -> date | None:
    t = text.strip()
    if not t or t == "-":
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(t, fmt).date()  # noqa: DTZ007 — a calendar date, no time
        except ValueError:
            continue
    raise ValueError(f"unparseable date {text!r}")


def _paisa(text: str) -> int:
    try:
        return rupees_to_paisa(Decimal(text.strip()))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"bad price {text!r}") from exc


# --- PURPOSE → components -----------------------------------------------------------------

_AMOUNT = r"(?:RS|RE)\.?\s*(\d+(?:\.\d+)?)"
_SPLIT_KEY = re.compile(r"FV\s*SPL|FVSPLT|FACE\s+VALUE\s+SPLIT|SUB-?DIVISION|CONSOLIDAT")
_SPLIT_AMOUNTS = re.compile(_AMOUNT + r".*?TO\s*.*?" + _AMOUNT)  # "RS10TORS5" too
_BONUS = re.compile(r"BONUS\s*(\d+)\s*:\s*(\d+)")
_DIV = re.compile(r"DIV(?!ISION)")
_DIV_AMOUNT = re.compile(r"DIV[^0-9]*?" + _AMOUNT)
_NOISE = re.compile(r"^(AGM|EGM|ANNUAL GENERAL MEETING|BOOK CLOSURE|AND)$")


def normalise_purpose(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().upper())


def parse_purpose(purpose: str) -> dict[str, object]:
    """Split/bonus/dividend components of an NSE PURPOSE string. Unknown → kind 'other'.

    A dividend whose amount is not stated (e.g. 'INTERIM DIVIDEND', '50%') is kind
    'dividend' with dividend_paisa None — the amount is never guessed."""
    p = normalise_purpose(purpose).replace("/-", " ")
    kinds: list[str] = []
    out: dict[str, object] = {}
    split_at = _SPLIT_KEY.search(p)
    if split_at:
        m = _SPLIT_AMOUNTS.search(p, split_at.start())
        if m:
            try:
                out["split_from_paisa"] = _paisa(m.group(1))
                out["split_to_paisa"] = _paisa(m.group(2))
                if out["split_from_paisa"] and out["split_to_paisa"]:
                    kinds.append("split")
            except ValueError:
                out.pop("split_from_paisa", None)
                out.pop("split_to_paisa", None)
    b = _BONUS.search(p)
    if b and int(b.group(1)) > 0 and int(b.group(2)) > 0:
        out["bonus_new"], out["bonus_held"] = int(b.group(1)), int(b.group(2))
        kinds.append("bonus")
    other = False
    amounts: list[int | None] = []
    for seg in re.split(r"[/+&]", p):
        seg = seg.strip(" -")
        if not seg or _NOISE.match(seg):
            continue
        if _SPLIT_KEY.search(seg) or _BONUS.search(seg):
            continue  # handled above
        if _DIV.search(seg):
            m = _DIV_AMOUNT.search(seg)
            try:
                amounts.append(_paisa(m.group(1)) if m else None)
            except ValueError:
                amounts.append(None)
        else:
            other = True
    if amounts:
        kinds.append("dividend")
        out["dividend_paisa"] = None if None in amounts else sum(a for a in amounts if a)
    if other or not kinds:
        kinds.append("other")
    out["kinds"] = tuple(kinds)
    return out


# --- CSV members ---------------------------------------------------------------------------


def _rows(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    return [
        {(k or "").strip().upper(): (v or "").strip() for k, v in r.items() if k is not None}
        for r in reader
    ]


Names = Mapping[str, str]  # NSE's symbol ON THAT DAY → our universe symbol (NSE:CURRENT)


def _as_names(universe: Names | set[str]) -> Names:
    if isinstance(universe, Mapping):
        return universe
    return {u.split(":", 1)[1]: u for u in universe}


def parse_bc(text: str, universe: Names | set[str]) -> Parsed[ActionRow]:
    """Corporate actions for universe symbols (any series), under the name NSE used that day
    (renames mapped to the current symbol). Rows without a parseable EX_DT are rejected."""
    names = _as_names(universe)
    rows: list[ActionRow] = []
    rejected = 0
    for r in _rows(text):
        symbol = names.get(r.get("SYMBOL", ""))
        if symbol is None:
            continue
        try:
            ex = parse_nse_date(r.get("EX_DT", ""))
            rec = parse_nse_date(r.get("RECORD_DT", ""))
        except ValueError:
            rejected += 1
            continue
        purpose = normalise_purpose(r.get("PURPOSE", ""))
        if ex is None or not purpose:
            rejected += 1
            continue
        parts = parse_purpose(purpose)
        rows.append(
            ActionRow(
                symbol=symbol,
                series=r.get("SERIES", ""),
                ex_date=ex,
                record_date=rec,
                purpose=purpose,
                kinds=parts["kinds"],  # type: ignore[arg-type]
                split_from_paisa=parts.get("split_from_paisa"),  # type: ignore[arg-type]
                split_to_paisa=parts.get("split_to_paisa"),  # type: ignore[arg-type]
                bonus_new=parts.get("bonus_new"),  # type: ignore[arg-type]
                bonus_held=parts.get("bonus_held"),  # type: ignore[arg-type]
                dividend_paisa=parts.get("dividend_paisa"),  # type: ignore[arg-type]
                source_symbol=r.get("SYMBOL", ""),
            )
        )
    return Parsed(rows, rejected)


# Equity series in preference order: EQ (normal), then trade-for-trade BE / BZ (new listings'
# first sessions, relistings, surveillance). T0 (T+0 settlement) and debt series never count.
PD_SERIES = ("EQ", "BE", "BZ")


def parse_pd(text: str, trade_date: date, universe: Names | set[str]) -> Parsed[BhavRow]:
    """Official as-traded prices (market N) for universe symbols, under the name NSE used that
    day. One row per symbol: EQ if present, else BE, else BZ. Within a series, a repeat with
    DIFFERENT values is ambiguous → the symbol is rejected; an identical repeat (NSE did this on
    2025-03-28) is kept once. Bad prices / OHLC order are rejected."""
    names = _as_names(universe)
    by_series: dict[tuple[str, str], BhavRow | None] = {}
    rejected = 0
    for r in _rows(text):
        series = r.get("SERIES", "")
        if r.get("MKT") != "N" or series not in PD_SERIES:
            continue
        symbol = names.get(r.get("SYMBOL", ""))
        if symbol is None:
            continue
        try:
            row = BhavRow(
                symbol=symbol,
                trade_date=trade_date,
                prev_close=_paisa(r["PREV_CL_PR"]),
                open=_paisa(r["OPEN_PRICE"]),
                high=_paisa(r["HIGH_PRICE"]),
                low=_paisa(r["LOW_PRICE"]),
                close=_paisa(r["CLOSE_PRICE"]),
                volume=int(r["NET_TRDQTY"]),
                series=series,
                source_symbol=r.get("SYMBOL", ""),
            )
        except (KeyError, ValueError):
            rejected += 1
            continue
        prices = (row.prev_close, row.open, row.high, row.low, row.close)
        if (
            min(prices) <= 0
            or row.volume < 0
            or row.high < max(row.open, row.close, row.low)
            or row.low > min(row.open, row.close)
        ):
            rejected += 1
            continue
        key = (symbol, series)
        if key in by_series:
            if by_series[key] != row:
                by_series[key] = None  # conflicting copies: ambiguous, never pick one
            continue
        by_series[key] = row
    out: list[BhavRow] = []
    dupes = 0
    for symbol in dict.fromkeys(k[0] for k in by_series):
        for series in PD_SERIES:
            if (symbol, series) in by_series:
                chosen = by_series[(symbol, series)]
                if chosen is None:
                    dupes += 1
                else:
                    out.append(chosen)
                break
    return Parsed(out, rejected + dupes)


@dataclass(frozen=True)
class SymbolChange:
    old: str
    new: str
    effective: date
    company: str


def parse_symbol_changes(text: str) -> list[SymbolChange]:
    """NSE's symbolchange.csv (static archive). Rows without a parseable date are skipped."""
    out: list[SymbolChange] = []
    for r in csv.reader(io.StringIO(text)):
        if len(r) < 4 or r[1].strip().upper() == "OLD SYMBOL":
            continue
        try:
            eff = datetime.strptime(r[3].strip(), "%d-%b-%Y").date()  # noqa: DTZ007 — a date
        except ValueError:
            continue
        old, new = r[1].strip(), r[2].strip()
        if old and new and old != new:
            out.append(SymbolChange(old, new, eff, r[0].strip()))
    return out


def names_on(d: date, universe: set[str], changes: Sequence[SymbolChange]) -> dict[str, str]:
    """The NSE symbol each universe member traded under ON `d`: walk renames effective after
    `d` backwards from the current name (chains included)."""
    by_new: dict[str, list[SymbolChange]] = {}
    for c in changes:
        by_new.setdefault(c.new, []).append(c)
    out: dict[str, str] = {}
    for u in universe:
        name, seen = u.split(":", 1)[1], set()
        while name not in seen:
            seen.add(name)
            later = [c for c in by_new.get(name, []) if c.effective > d]
            if not later:
                break
            name = max(later, key=lambda c: c.effective).old
        out[name] = u
    return out


def _member_date(name: str) -> date | None:
    m = re.search(r"(\d{6}(?:\d{2})?)\.csv$", name, re.IGNORECASE)
    if not m:
        return None
    digits = m.group(1)
    fmt = "%d%m%Y" if len(digits) == 8 else "%d%m%y"
    try:
        return datetime.strptime(digits, fmt).date()  # noqa: DTZ007 — a calendar date
    except ValueError:
        return None


def read_members(
    blob: bytes, max_member_bytes: int, expected: date | None = None
) -> tuple[str, str]:
    """The Bc and Pd CSV texts from one PR zip. Missing or oversized members → error.

    `expected` is the trading date the zip was requested for: NSE's archive mislabels some
    days (PR020118.zip holds the 2019-01-02 files), so every member's own date must match it,
    or the whole zip is refused — never stored under the wrong date."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise NseArchiveError("not a zip file") from exc
    found: dict[str, zipfile.ZipInfo] = {}
    for info in zf.infolist():
        name = info.filename.rsplit("/", 1)[-1]
        for key, pattern in (("bc", _BC_NAME), ("pd", _PD_NAME)):
            if pattern.match(name):
                found[key] = info
    missing = [k for k in ("bc", "pd") if k not in found]
    if missing:
        raise NseArchiveError(f"zip lacks member(s): {', '.join(missing)}")
    if expected is not None:
        for key in ("bc", "pd"):
            got = _member_date(found[key].filename)
            if got != expected:
                raise NseArchiveMislabeled(
                    f"zip for {expected} holds {key} file dated {got} ({found[key].filename})"
                )
    texts = []
    for key in ("bc", "pd"):
        info = found[key]
        if info.file_size > max_member_bytes:
            raise NseArchiveError(f"{key} member too large: {info.file_size} bytes")
        with zf.open(info) as fh:
            data = fh.read(max_member_bytes + 1)
        if len(data) > max_member_bytes:
            raise NseArchiveError(f"{key} member inflates past {max_member_bytes} bytes")
        texts.append(data.decode("utf-8", errors="replace"))
    return texts[0], texts[1]


# --- fetching -----------------------------------------------------------------------------

Sleep = Callable[[float], Awaitable[None]]


class NseArchiveClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        settings: Settings,
        limiter: RateLimiter,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._http = http
        self._s = settings
        self._limiter = limiter
        self._sleep = sleep

    async def fetch_text(self, url: str) -> str:
        """A small archive text file (e.g. symbolchange.csv). Errors raise — never guessed."""
        await self._limiter.acquire()
        try:
            resp = await self._http.get(
                url,
                headers={"User-Agent": self._s.nse_user_agent},
                timeout=self._s.nse_archive_timeout_seconds,
            )
        except httpx.TransportError as exc:
            raise NseArchiveError(f"NSE archive unreachable: {type(exc).__name__}") from exc
        if resp.status_code in (401, 403):
            raise NseArchiveRefused(f"NSE archive refused ({resp.status_code})")
        if resp.status_code != 200:
            raise NseArchiveError(f"NSE archive HTTP {resp.status_code} for {url}")
        return resp.content.decode("utf-8", errors="replace")

    async def fetch_zip(self, d: date) -> bytes | None:
        """The day's PR zip, or None when the archive has no file for it (404)."""
        url = archive_url(self._s.nse_archive_base_url, d)
        max_bytes = int(self._s.nse_archive_max_zip_mb * 1024 * 1024)
        attempts = self._s.nse_archive_retries + 1
        last = ""
        for attempt in range(attempts):
            await self._limiter.acquire()
            try:
                resp = await self._http.get(
                    url,
                    headers={"User-Agent": self._s.nse_user_agent},
                    timeout=self._s.nse_archive_timeout_seconds,
                )
            except httpx.TransportError as exc:
                last = type(exc).__name__
            else:
                if resp.status_code == 200:
                    if len(resp.content) > max_bytes:
                        raise NseArchiveError(f"zip too large: {len(resp.content)} bytes")
                    return resp.content
                if resp.status_code == 404:
                    return None
                if resp.status_code in (401, 403):
                    raise NseArchiveRefused(f"NSE archive refused ({resp.status_code})")
                if resp.status_code != 429 and resp.status_code < 500:
                    raise NseArchiveError(f"NSE archive HTTP {resp.status_code}")
                last = f"HTTP {resp.status_code}"
            if attempt < attempts - 1:
                await self._sleep(self._s.nse_archive_retry_backoff_seconds * (2**attempt))
        raise NseArchiveError(f"NSE archive failed after {attempts} attempts: {last}")
