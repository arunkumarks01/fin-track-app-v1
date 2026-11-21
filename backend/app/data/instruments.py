"""Kite instrument master + the F&O universe we ingest.

Universe (SCOPE §1): every stock with an NFO stock future *and* an NSE cash listing, plus
the configured indices. Index futures (NIFTY, BANKNIFTY, …) have no NSE EQ listing, so
they drop out without a hardcoded exclusion list. Fail closed: a malformed master row in
a segment we use, or a configured index missing from the master, aborts the refresh.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.core.money import rupees_to_paisa

log = logging.getLogger(__name__)

KEEP_EXCHANGES = frozenset({"NSE", "NFO"})
INDICES_SEGMENT = "INDICES"
STOCK_FUTURES_SEGMENT = "NFO-FUT"
GIFT_NIFTY_SYMBOL = "NSEIX:GIFT NIFTY"
INDIA_VIX_SYMBOL = "NSE:INDIA VIX"
REFERENCE_INDEX_SYMBOLS = (GIFT_NIFTY_SYMBOL, INDIA_VIX_SYMBOL)

EXPECTED_HEADER = [
    "instrument_token",
    "exchange_token",
    "tradingsymbol",
    "name",
    "last_price",
    "expiry",
    "strike",
    "tick_size",
    "lot_size",
    "instrument_type",
    "segment",
    "exchange",
]


class InstrumentsError(ValueError):
    pass


@dataclass(frozen=True)
class Instrument:
    instrument_token: int
    exchange_token: int
    tradingsymbol: str
    name: str | None
    exchange: str
    segment: str
    instrument_type: str
    expiry: date | None
    strike: int | None  # paisa
    tick_size: int  # paisa
    lot_size: int

    @property
    def symbol(self) -> str:
        return f"{self.exchange}:{self.tradingsymbol}"


@dataclass(frozen=True)
class UniverseMember:
    symbol: str
    instrument_token: int
    kind: str  # 'equity' | 'index'


def _keep(row: dict[str, str]) -> bool:
    return (
        row["exchange"] in KEEP_EXCHANGES
        or (row["exchange"] == "BSE" and row["segment"] == INDICES_SEGMENT)
        or f"{row['exchange']}:{row['tradingsymbol']}" == GIFT_NIFTY_SYMBOL
    )


def _opt_paisa(raw: str) -> int | None:
    raw = raw.strip()
    return None if raw in ("", "0", "0.0") else rupees_to_paisa(raw)


def parse_instruments_csv(text: str) -> list[Instrument]:
    reader = csv.DictReader(io.StringIO(text))
    if [h.strip() for h in (reader.fieldnames or [])] != EXPECTED_HEADER:
        raise InstrumentsError(f"unexpected instruments header: {reader.fieldnames}")
    out: list[Instrument] = []
    errors: list[str] = []
    for line_no, row in enumerate(reader, start=2):
        if not _keep(row):
            continue
        try:
            out.append(
                Instrument(
                    instrument_token=int(row["instrument_token"]),
                    exchange_token=int(row["exchange_token"]),
                    tradingsymbol=row["tradingsymbol"],
                    name=row["name"] or None,
                    exchange=row["exchange"],
                    segment=row["segment"],
                    instrument_type=row["instrument_type"],
                    expiry=date.fromisoformat(row["expiry"]) if row["expiry"] else None,
                    strike=_opt_paisa(row["strike"]),
                    tick_size=rupees_to_paisa(row["tick_size"] or "0"),
                    lot_size=int(row["lot_size"] or 0),
                )
            )
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"line {line_no}: {exc}")
    if errors:
        raise InstrumentsError(f"{len(errors)} malformed instrument rows, e.g. {errors[:3]}")
    if not out:
        raise InstrumentsError("instrument master is empty after filtering")
    return out


def build_universe(instruments: list[Instrument], index_symbols: list[str]) -> list[UniverseMember]:
    nse_equity = {
        i.tradingsymbol: i
        for i in instruments
        if i.exchange == "NSE" and i.segment == "NSE" and i.instrument_type == "EQ"
    }
    fno_underlyings = {
        i.name
        for i in instruments
        if i.segment == STOCK_FUTURES_SEGMENT and i.instrument_type == "FUT" and i.name
    }
    members = [
        UniverseMember(nse_equity[n].symbol, nse_equity[n].instrument_token, "equity")
        for n in sorted(fno_underlyings & nse_equity.keys())
    ]
    indices = {i.symbol: i for i in instruments if i.segment == INDICES_SEGMENT}
    missing = [s for s in index_symbols if s not in indices]
    if missing:
        raise InstrumentsError(f"configured indices not in the instrument master: {missing}")
    members += [UniverseMember(s, indices[s].instrument_token, "index") for s in index_symbols]
    return members


async def historical_members(
    conn: Any, symbols: list[str] | None, include_reference_indices: bool
) -> list[UniverseMember]:
    rows = await conn.fetch(
        """SELECT symbol, instrument_token, kind FROM (
               SELECT symbol, instrument_token, kind FROM app.universe WHERE active
               UNION ALL
               SELECT r.symbol, i.instrument_token, 'index'
               FROM unnest($2::text[]) AS r(symbol)
               JOIN app.instruments i ON i.exchange = split_part(r.symbol, ':', 1)
                                     AND i.tradingsymbol = split_part(r.symbol, ':', 2)
               WHERE NOT EXISTS (SELECT 1 FROM app.universe u WHERE u.symbol = r.symbol)
           ) members
           WHERE $1::text[] IS NULL OR symbol = ANY($1)
           ORDER BY kind DESC, symbol""",
        symbols,
        list(REFERENCE_INDEX_SYMBOLS) if include_reference_indices else [],
    )
    return [UniverseMember(row["symbol"], row["instrument_token"], row["kind"]) for row in rows]


async def replace_instruments(conn: Any, instruments: list[Instrument], now: datetime) -> int:
    """Atomically replace the master snapshot."""
    async with conn.transaction():
        await conn.execute("DELETE FROM app.instruments")
        await conn.copy_records_to_table(
            "instruments",
            schema_name="app",
            columns=[
                "instrument_token",
                "exchange_token",
                "tradingsymbol",
                "name",
                "exchange",
                "segment",
                "instrument_type",
                "expiry",
                "strike",
                "tick_size",
                "lot_size",
                "refreshed_at",
            ],
            records=[
                (
                    i.instrument_token,
                    i.exchange_token,
                    i.tradingsymbol,
                    i.name,
                    i.exchange,
                    i.segment,
                    i.instrument_type,
                    i.expiry,
                    i.strike,
                    i.tick_size,
                    i.lot_size,
                    now,
                )
                for i in instruments
            ],
        )
    return len(instruments)


async def sync_universe(conn: Any, members: list[UniverseMember], now: datetime) -> dict[str, int]:
    """Upsert active members; deactivate (never delete) symbols that left the list."""
    async with conn.transaction():
        before = {
            r["symbol"]: r["active"]
            for r in await conn.fetch("SELECT symbol, active FROM app.universe")
        }
        await conn.executemany(
            """
            INSERT INTO app.universe
                (symbol, instrument_token, kind, active, added_at, refreshed_at)
            VALUES ($1, $2, $3, true, $4, $4)
            ON CONFLICT (symbol) DO UPDATE SET instrument_token = EXCLUDED.instrument_token,
                kind = EXCLUDED.kind, active = true, refreshed_at = EXCLUDED.refreshed_at
            """,
            [(m.symbol, m.instrument_token, m.kind, now) for m in members],
        )
        current = {m.symbol for m in members}
        gone = [s for s, active in before.items() if active and s not in current]
        if gone:
            await conn.execute(
                "UPDATE app.universe SET active = false, refreshed_at = $2 WHERE symbol = ANY($1)",
                gone,
                now,
            )
    return {
        "active": len(current),
        "added": len(current - before.keys()),
        "reactivated": len([s for s in current if s in before and not before[s]]),
        "deactivated": len(gone),
    }
