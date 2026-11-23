"""Option-chain snapshots every 5 minutes (slice 1d, DEC-1D-CHAIN / DEC-1D-GREEKS).

Narrow and live: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY plus the top-N stock underlyings by
20-session option premium turnover (ranked from the F&O history, slice 1f), the current and next
expiry, ATM +-15 strikes, calls and puts. One snapshot = one batch of Kite /quote calls (1 req/s).
RAW values only (price, volume, open interest, best bid/ask, last trade time) with the underlying's
spot and its near-month future at the same moment; IV and greeks are Phase 2's job, recomputable
from these. Fail closed: no Kite session, or a quote the client can't parse, records an error row
for that underlying and stores nothing guessed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.broker.kite.client import KiteClient, KiteError
from app.core.clock import Clock
from app.core.config import Settings
from app.core.money import rupees_to_paisa
from app.core.tradingdate import MARKET_TZ

log = logging.getLogger(__name__)

INT4_MAX = 2_147_483_647


@dataclass(frozen=True)
class Contract:
    token: int
    tradingsymbol: str
    expiry: date
    strike_paisa: int
    opt_type: str  # CE | PE

    @property
    def key(self) -> str:
        return f"NFO:{self.tradingsymbol}"


@dataclass
class Underlying:
    name: str
    spot_key: str
    fut_key: str | None
    fut_expiry: date | None
    contracts: list[Contract]  # the chosen strikes of the chosen expiries (after selection)


def select_strikes(
    contracts: list[Contract], spot_paisa: int, expiries: int, each_side: int
) -> list[Contract]:
    """The first `expiries` expiries; per expiry the strike nearest the spot (ATM) and
    `each_side` strikes above and below it; calls and puts of every chosen strike."""
    chosen: list[Contract] = []
    for exp in sorted({c.expiry for c in contracts})[:expiries]:
        in_exp = [c for c in contracts if c.expiry == exp]
        strikes = sorted({c.strike_paisa for c in in_exp})
        if not strikes:
            continue
        atm = min(range(len(strikes)), key=lambda i: (abs(strikes[i] - spot_paisa), strikes[i]))
        keep = set(strikes[max(0, atm - each_side) : atm + each_side + 1])
        chosen += [c for c in in_exp if c.strike_paisa in keep]
    return chosen


def _paisa(v: Any) -> int | None:
    """A quote price as integer paisa; None when absent or not representable exactly."""
    if v is None:
        return None
    try:
        p = int(rupees_to_paisa(v if isinstance(v, Decimal) else Decimal(str(v))))
    except (ValueError, TypeError, ArithmeticError):
        return None
    return p if 0 <= p <= INT4_MAX else None


def _int(v: Any) -> int | None:
    return int(v) if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _when(v: Any) -> datetime | None:
    """Kite times are 'YYYY-MM-DD HH:MM:SS' in IST; a zero date means 'none'."""
    if not isinstance(v, str) or v.startswith("0000") or v.startswith("0001"):
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d %H:%M:%S").replace(tzinfo=MARKET_TZ)
    except ValueError:
        return None


def option_row(
    snapshot_ts: datetime, underlying: str, c: Contract, q: dict[str, Any]
) -> tuple[Any, ...] | None:
    """One stored row from one quote, or None when the quote has no usable price."""
    ltp = _paisa(q.get("last_price"))
    if ltp is None:
        return None
    depth = q.get("depth") or {}
    buy = (depth.get("buy") or [{}])[0] if isinstance(depth, dict) else {}
    sell = (depth.get("sell") or [{}])[0] if isinstance(depth, dict) else {}
    return (
        snapshot_ts, underlying, c.token, c.expiry, c.strike_paisa, c.opt_type, ltp,
        _int(q.get("volume")), _int(q.get("oi")),
        _paisa(buy.get("price")) or None, _int(buy.get("quantity")),
        _paisa(sell.get("price")) or None, _int(sell.get("quantity")),
        _when(q.get("last_trade_time")),
    )  # fmt: skip


@dataclass
class ChainReport:
    snapshot_ts: datetime | None = None
    underlyings: dict[str, str] = field(default_factory=dict)  # name -> ok/partial/error
    contracts_requested: int = 0
    contracts_stored: int = 0
    requests: int = 0
    skipped: str | None = None
    stopped_reason: str | None = None

    def totals(self) -> dict[str, Any]:
        return {
            "snapshot_ts": self.snapshot_ts and self.snapshot_ts.isoformat(),
            "underlyings": self.underlyings,
            "contracts_requested": self.contracts_requested,
            "contracts_stored": self.contracts_stored,
            "requests": self.requests,
            "skipped": self.skipped,
            "inserted": self.contracts_stored,
        }


class OptionChain:
    def __init__(
        self, pool: Any, kite: KiteClient | None, settings: Settings, clock: Clock
    ) -> None:
        self._pool = pool
        self._kite = kite
        self._s = settings
        self._clock = clock
        self._ranked: tuple[tuple[int, int], list[str]] | None = None  # (iso year, week) -> names
        self.last = ChainReport()

    # --- scope -------------------------------------------------------------------------------

    async def _rank_stocks(self, conn: Any, today: date) -> list[str]:
        """Top stock underlyings by the last N sessions' option premium turnover (re-ranked weekly;
        an explicit CHAIN_STOCK_OVERRIDE wins). Empty when there is no F&O history to rank by."""
        if self._s.chain_stock_override:
            return list(self._s.chain_stock_override)[: self._s.chain_stock_count]
        week = today.isocalendar()[:2]
        if self._ranked and self._ranked[0] == (week[0], week[1]):
            return self._ranked[1]
        rows = await conn.fetch(
            """WITH days AS (SELECT DISTINCT trade_date FROM app.fo_daily
                             WHERE kind = 'STO' AND trade_date < $1
                             ORDER BY trade_date DESC LIMIT $2),
                    lots AS (SELECT name, max(lot_size) AS lot FROM app.instruments
                             WHERE segment = 'NFO-OPT' GROUP BY name)
               SELECT f.underlying, sum(f.contracts::numeric * f.close_paisa * l.lot) AS turnover
               FROM app.fo_daily f JOIN lots l ON l.name = f.underlying
               WHERE f.kind = 'STO' AND f.trade_date IN (SELECT trade_date FROM days)
               GROUP BY f.underlying ORDER BY turnover DESC, f.underlying LIMIT $3""",
            today,
            self._s.chain_rank_sessions,
            self._s.chain_stock_count,
        )
        names = [r["underlying"] for r in rows]
        if not names and self._s.chain_stock_count:
            log.warning("option chain: no F&O history to rank stocks; indices only")
        self._ranked = ((week[0], week[1]), names)
        return names

    async def _universe(self, conn: Any, today: date) -> list[Underlying]:
        names = list(self._s.chain_index_underlyings) + await self._rank_stocks(conn, today)
        out: list[Underlying] = []
        for name in names:
            opts = await conn.fetch(
                """SELECT instrument_token, tradingsymbol, expiry, strike, instrument_type
                   FROM app.instruments WHERE segment = 'NFO-OPT' AND name = $1
                     AND expiry >= $2 AND strike IS NOT NULL""",
                name,
                today,
            )
            fut = await conn.fetchrow(
                """SELECT tradingsymbol, expiry FROM app.instruments
                   WHERE segment = 'NFO-FUT' AND name = $1 AND expiry >= $2
                   ORDER BY expiry LIMIT 1""",
                name,
                today,
            )
            if not opts:
                log.warning("option chain: no listed options for %s", name)
                continue
            spot = self._s.chain_index_spot.get(name) or f"NSE:{name}"
            out.append(
                Underlying(
                    name,
                    spot,
                    f"NFO:{fut['tradingsymbol']}" if fut else None,
                    fut["expiry"] if fut else None,
                    [
                        Contract(
                            int(o["instrument_token"]),
                            o["tradingsymbol"],
                            o["expiry"],
                            int(o["strike"]),
                            o["instrument_type"],
                        )
                        for o in opts
                        if o["instrument_type"] in ("CE", "PE")
                    ],
                )
            )
        return out

    # --- one snapshot ------------------------------------------------------------------------

    def in_window(self, now: datetime) -> bool:
        local = now.astimezone(MARKET_TZ)
        return local.weekday() < 5 and self._s.chain_start <= local.time() <= self._s.chain_end

    async def snapshot(self) -> ChainReport:
        now = self._clock.now()
        report = self.last = ChainReport()
        if not self.in_window(now):
            report.skipped = "outside the chain window"
            return report
        if self._kite is None:
            report.stopped_reason = "Kite is not configured"
            return report
        local = now.astimezone(MARKET_TZ)
        ts = local.replace(minute=local.minute - local.minute % 5, second=0, microsecond=0)
        report.snapshot_ts = ts
        try:
            async with self._pool.acquire() as conn:
                universe = await self._universe(conn, local.date())
            if not universe:
                report.stopped_reason = "no underlyings to snapshot (instrument master empty?)"
                return report
            # 1) spots and near-month futures, one batch
            need = [u.spot_key for u in universe] + [u.fut_key for u in universe if u.fut_key]
            quotes = await self._quote(need, report)
            # 2) pick ATM +-N strikes per underlying, then quote them all
            plan: list[tuple[Underlying, int | None, list[Contract]]] = []
            for u in universe:
                spot = _paisa((quotes.get(u.spot_key) or {}).get("last_price"))
                if spot is None or spot <= 0:
                    report.underlyings[u.name] = "error"
                    await self._meta(ts, u, None, None, 0, "error", "no spot price in the quote")
                    continue
                chosen = select_strikes(
                    u.contracts, spot, self._s.chain_expiries, self._s.chain_strikes_each_side
                )
                plan.append((u, spot, chosen))
            keys = [c.key for _, _, cs in plan for c in cs]
            report.contracts_requested = len(keys)
            opt_quotes: dict[str, dict[str, Any]] = {}
            for i in range(0, len(keys), self._s.chain_quote_batch):
                opt_quotes |= await self._quote(keys[i : i + self._s.chain_quote_batch], report)
            # 3) store raw values, one transaction for the whole snapshot
            async with self._pool.acquire() as conn, conn.transaction():
                for u, spot, chosen in plan:
                    rows = [
                        r
                        for c in chosen
                        if c.key in opt_quotes
                        and (r := option_row(ts, u.name, c, opt_quotes[c.key])) is not None
                    ]
                    fut = _paisa((quotes.get(u.fut_key or "") or {}).get("last_price"))
                    status = "ok" if len(rows) == len(chosen) and chosen else "partial"
                    if not rows:
                        status = "error"
                    detail = (
                        None if status == "ok" else f"{len(rows)} of {len(chosen)} contracts quoted"
                    )
                    await self._meta(ts, u, spot, fut, len(rows), status, detail, conn=conn)
                    if rows:
                        await conn.executemany(
                            """INSERT INTO app.option_snapshots
                                   (snapshot_ts, underlying, instrument_token, expiry, strike_paisa,
                                    opt_type, ltp, volume, oi, bid, bid_qty, ask, ask_qty, ltt)
                               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                               ON CONFLICT DO NOTHING""",
                            rows,
                        )
                    report.underlyings[u.name] = status
                    report.contracts_stored += len(rows)
        except KiteError as exc:
            report.stopped_reason = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("option chain snapshot failed: %s", report.stopped_reason)
        return report

    async def _quote(self, keys: list[str], report: ChainReport) -> dict[str, dict[str, Any]]:
        kite = self._kite
        if kite is None:
            raise KiteError("Kite is not configured")
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(keys), 500):
            report.requests += 1
            out |= await kite.quote(keys[i : i + 500])
        return out

    async def _meta(
        self,
        ts: datetime,
        u: Underlying,
        spot: int | None,
        fut: int | None,
        contracts: int,
        status: str,
        detail: str | None,
        conn: Any = None,
    ) -> None:
        sql = """INSERT INTO app.option_snapshot_meta
                     (snapshot_ts, underlying, spot_paisa, fut_paisa, fut_expiry, contracts,
                      status, detail)
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8) ON CONFLICT DO NOTHING"""
        args = (ts, u.name, spot, fut, u.fut_expiry, contracts, status, detail)
        if conn is not None:
            await conn.execute(sql, *args)
            return
        async with self._pool.acquire() as c:
            await c.execute(sql, *args)
