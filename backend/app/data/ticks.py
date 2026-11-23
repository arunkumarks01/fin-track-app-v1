"""Live tick capture (slice 1d, DEC-1D-TICKS): Kite WebSocket -> compact `app.ticks` rows.

One in-process task per trading day (the scheduler starts it before the open). It subscribes the
universe (+ INDIA VIX and GIFT NIFTY) in Full mode, parses each binary message with the strict
parser, and writes in batches. Rules, all fail-closed:
  - no valid Kite session  -> the run is recorded 'no_session' and nothing connects;
  - a connection that drops or goes silent is re-opened with backoff until the window ends;
  - a tick whose values don't fit their columns, or whose token we don't know, is counted as
    rejected — never clipped, never stored under another instrument;
  - if the database is unreachable the rows stay buffered and are retried; past the buffer cap
    the OLDEST are dropped and counted (said honestly in the run record), memory never grows
    without bound;
  - the access token only ever appears in the connect URL, which is never logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Protocol

import websockets

from app.core.clock import Clock
from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ
from app.data.intraday import ensure_sids
from app.data.kite_ticks import INT4_MAX, Tick, TickParseError, parse_message

log = logging.getLogger(__name__)

INT2_MAX = 32_767
COLUMNS = (
    "ts", "sid", "ltp", "ltq", "atp", "volume", "buy_qty", "sell_qty", "oi", "ltt", "ext",
    *(f"{side}{i}{k}" for side in "ba" for i in range(1, 6) for k in "pqo"),
)  # fmt: skip


class HasSession(Protocol):
    """All the capture needs from the Kite session store."""

    def current(self) -> Any: ...


Connect = Callable[..., Any]  # websockets.connect-compatible (an async context manager)


def tick_row(t: Tick, sid: int, recv: datetime) -> tuple[Any, ...] | None:
    """The row for one tick, or None when any value doesn't fit its column."""
    ints4 = [t.ltp, t.last_qty, t.avg_price]
    depth: list[int | None] = []
    for lv in (*t.bids, *t.asks) if t.kind == "full" else ():
        depth += [lv.price, lv.qty, lv.orders]
    if any(v is not None and not 0 <= v <= INT4_MAX for v in ints4 + depth[0::3] + depth[1::3]):
        return None
    if any(o is not None and not 0 <= o <= INT2_MAX for o in depth[2::3]):
        return None
    if t.ltp <= 0:
        return None
    if t.kind != "full":
        depth = [None] * 30
    return (
        recv, sid, t.ltp, t.last_qty, t.avg_price, t.volume, t.buy_qty, t.sell_qty,
        t.oi, t.last_trade_time, t.exchange_time, *depth,
    )  # fmt: skip


@dataclass
class CaptureStats:
    status: str = "idle"  # idle | running | ok | failed | no_session
    session_date: date | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    instruments: int = 0
    ticks: int = 0
    rejected: int = 0
    dropped: int = 0  # rows lost to a full buffer while the database was unreachable
    unknown_tokens: int = 0
    reconnects: int = 0
    connected: bool = False
    last_tick_at: datetime | None = None
    detail: str | None = None
    buffered: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            k: (v.isoformat() if isinstance(v, datetime | date) else v)
            for k, v in self.__dict__.items()
        }


@dataclass
class TickBuffer:
    max_rows: int
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    dropped: int = 0

    def add(self, row: tuple[Any, ...]) -> None:
        self.rows.append(row)
        if len(self.rows) > self.max_rows:
            over = len(self.rows) - self.max_rows
            del self.rows[:over]  # oldest first
            self.dropped += over


class TickCapture:
    def __init__(
        self,
        pool: Any,
        sessions: HasSession,
        settings: Settings,
        clock: Clock,
        *,
        connect: Connect = websockets.connect,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._pool = pool
        self._sessions = sessions
        self._s = settings
        self._clock = clock
        self._connect = connect
        self._sleep = sleep
        self._stop = asyncio.Event()
        self.stats = CaptureStats()

    def request_stop(self) -> None:
        self._stop.set()

    # --- what to subscribe -------------------------------------------------------------------

    async def instruments(self) -> dict[int, int]:
        """token -> sid for the active universe and the configured extras."""
        async with self._pool.acquire() as conn:
            uni = await conn.fetch("SELECT symbol, instrument_token FROM app.universe WHERE active")
            extras: list[tuple[str, int]] = []
            for sym in self._s.ticks_extra_symbols:
                ex, _, ts = sym.partition(":")
                tok = await conn.fetchval(
                    "SELECT instrument_token FROM app.instruments "
                    "WHERE exchange = $1 AND tradingsymbol = $2",
                    ex,
                    ts,
                )
                if tok is None:
                    log.warning("ticks: extra instrument %s is not in the instrument master", sym)
                else:
                    extras.append((sym, int(tok)))
            by_symbol = [(r["symbol"], int(r["instrument_token"])) for r in uni] + extras
            sids = await ensure_sids(conn, [s for s, _ in by_symbol])
        return {tok: sids[sym] for sym, tok in by_symbol}

    # --- the day's run -----------------------------------------------------------------------

    def window(self, now: datetime) -> tuple[datetime, datetime]:
        d = now.astimezone(MARKET_TZ).date()
        return (
            datetime.combine(d, self._s.ticks_capture_start, MARKET_TZ),
            datetime.combine(d, self._s.ticks_capture_end, MARKET_TZ),
        )

    async def run(self) -> CaptureStats:
        now = self._clock.now()
        st = self.stats = CaptureStats(
            status="running", session_date=now.astimezone(MARKET_TZ).date(), started_at=now
        )
        run_id: int | None = None
        try:
            if self._sessions.current() is None:
                st.status, st.detail = "no_session", "no valid Kite session — log in first"
                log.warning("ticks: %s", st.detail)
                return st
            tokens = await self.instruments()
            st.instruments = len(tokens)
            if not tokens:
                st.status, st.detail = "failed", "no instruments to subscribe"
                return st
            async with self._pool.acquire() as conn:
                run_id = await conn.fetchval(
                    """INSERT INTO app.tick_capture_runs (session_date, started_at, status,
                           instruments) VALUES ($1, $2, 'running', $3) RETURNING id""",
                    st.session_date,
                    now,
                    st.instruments,
                )
            _, until = self.window(now)
            await self._capture(tokens, until, st)
            st.status = "ok"
        except asyncio.CancelledError:
            st.status, st.detail = "failed", "cancelled"
            raise
        except Exception as exc:  # recorded, never silent
            st.status, st.detail = "failed", f"{type(exc).__name__}: {exc}"[:300]
            log.exception("tick capture failed")
        finally:
            st.connected = False
            st.ended_at = self._clock.now()
            if run_id is not None:
                try:
                    async with self._pool.acquire() as conn:
                        await conn.execute(
                            """UPDATE app.tick_capture_runs SET ended_at = $2, status = $3,
                                   ticks = $4, rejected = $5, reconnects = $6, detail = $7
                               WHERE id = $1""",
                            run_id,
                            st.ended_at,
                            st.status,
                            st.ticks,
                            st.rejected + st.dropped + st.unknown_tokens,
                            st.reconnects,
                            st.detail
                            or (
                                f"{st.dropped} rows dropped (database unreachable)"
                                if st.dropped
                                else None
                            ),
                        )
                except Exception:  # the run record is best effort at shutdown
                    log.exception("could not close the tick capture run record")
        return st

    async def _capture(self, tokens: dict[int, int], until: datetime, st: CaptureStats) -> None:
        buf = TickBuffer(self._s.ticks_buffer_max_rows)
        backoff = self._s.ticks_reconnect_backoff_seconds
        try:
            while not self._stop.is_set() and self._clock.now() < until:
                session = self._sessions.current()
                if session is None:
                    st.detail = "Kite session ended during capture"
                    break
                url = (
                    f"{self._s.kite_ws_url}?api_key={self._s.kite_api_key.get_secret_value()}"  # type: ignore[union-attr]
                    f"&access_token={session.access_token}"
                )
                try:
                    async with self._connect(url, max_size=None, ping_interval=None) as ws:
                        st.connected = True
                        backoff = self._s.ticks_reconnect_backoff_seconds
                        ids = list(tokens)
                        await ws.send(json.dumps({"a": "subscribe", "v": ids}))
                        await ws.send(json.dumps({"a": "mode", "v": ["full", ids]}))
                        await self._pump(ws, tokens, until, buf, st)
                except (OSError, TimeoutError, websockets.WebSocketException) as exc:
                    st.connected = False
                    if self._stop.is_set():
                        break  # we asked to stop; the close that followed is not a fault
                    st.reconnects += 1
                    log.warning(
                        "ticks: connection lost (%s); reconnect in %.0fs",
                        type(exc).__name__,
                        backoff,
                    )
                    await self._flush(buf, st)
                    await self._sleep(backoff)
                    backoff = min(backoff * 2, self._s.ticks_reconnect_max_seconds)
                else:
                    st.connected = False
                    if not self._stop.is_set() and self._clock.now() < until:
                        st.reconnects += 1  # the server closed cleanly: open it again
                        await self._sleep(backoff)
        finally:
            await self._flush(buf, st, final=True)
            st.dropped = buf.dropped

    async def _pump(
        self, ws: Any, tokens: dict[int, int], until: datetime, buf: TickBuffer, st: CaptureStats
    ) -> None:
        last_flush = self._clock.now()
        slice_s = min(1.0, self._s.ticks_idle_timeout_seconds)
        silent = 0.0
        while not self._stop.is_set() and self._clock.now() < until:
            try:
                # short reads, so a stop request is noticed within a second
                msg = await asyncio.wait_for(ws.recv(), timeout=slice_s)
            except TimeoutError as exc:
                silent += slice_s
                if silent >= self._s.ticks_idle_timeout_seconds:
                    raise TimeoutError("no message (not even a heartbeat) — reconnecting") from exc
                continue
            silent = 0.0
            now = self._clock.now()
            if isinstance(msg, str):  # Kite's text messages: errors, order updates
                log.info("ticks: text message %s", msg[:160])
            else:
                try:
                    ticks = parse_message(msg)
                except TickParseError as exc:
                    st.rejected += 1
                    log.warning("ticks: malformed message: %s", exc)
                    ticks = []
                for t in ticks:
                    sid = tokens.get(t.token)
                    if sid is None:
                        st.unknown_tokens += 1
                        continue
                    row = tick_row(t, sid, now)
                    if row is None:
                        st.rejected += 1
                        continue
                    buf.add(row)
                    st.ticks += 1
                    st.last_tick_at = now
            st.buffered = len(buf.rows)
            if (
                len(buf.rows) >= self._s.ticks_flush_rows
                or (now - last_flush).total_seconds() >= self._s.ticks_flush_seconds
            ):
                await self._flush(buf, st)
                last_flush = now

    async def _flush(self, buf: TickBuffer, st: CaptureStats, final: bool = False) -> None:
        if not buf.rows:
            return
        batch = buf.rows[:]
        try:
            async with self._pool.acquire() as conn:
                await conn.copy_records_to_table(
                    "ticks", schema_name="app", columns=COLUMNS, records=batch
                )
        except Exception as exc:  # the database is unreachable: keep the rows and retry
            log.warning(
                "ticks: could not write %d rows (%s); kept in memory",
                len(batch),
                type(exc).__name__,
            )
            if final:
                st.detail = f"{len(buf.rows)} buffered rows were not written: {type(exc).__name__}"
            return
        del buf.rows[: len(batch)]  # only what was written; newer rows stay
        st.buffered = len(buf.rows)


def capture_window_times(settings: Settings) -> tuple[time, time]:
    return settings.ticks_capture_start, settings.ticks_capture_end
