"""Slice 1d: live tick capture against a real local WebSocket server and the test database."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
import websockets

from app.core.tradingdate import MARKET_TZ
from app.data.instruments import UniverseMember, sync_universe
from app.data.kite_ticks import build_full_packet, build_message
from app.data.ticks import COLUMNS, TickBuffer, TickCapture, tick_row
from app.db.migrate import migrate
from tests.conftest import build_settings
from tests.fixtures.clock import FrozenClock

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 23, 10, 0, tzinfo=MARKET_TZ)  # a Monday, inside the capture window
TOKEN_A, TOKEN_B = 408065, 738561


@dataclass
class FakeSession:
    access_token: str = "secret-token-123"


class Sessions:
    def __init__(self, session: FakeSession | None = None, *, missing: bool = False) -> None:
        self.s = None if missing else (session or FakeSession())

    def current(self) -> FakeSession | None:
        return self.s


@pytest.fixture
async def pool(test_dsn: str, db: Any) -> AsyncIterator[Any]:
    await migrate(db)
    await sync_universe(
        db,
        [
            UniverseMember("NSE:INFY", TOKEN_A, "equity"),
            UniverseMember("NSE:RELIANCE", TOKEN_B, "equity"),
        ],
        NOW,
    )
    p = await asyncpg.create_pool(test_dsn, min_size=1, max_size=3)
    try:
        yield p
    finally:
        await p.close()


def _settings(dsn: str, port: int, **kw: Any) -> Any:
    opts: dict[str, Any] = {
        "kite_api_key": "the-api-key",
        "kite_ws_url": f"ws://127.0.0.1:{port}",
        "ticks_extra_symbols": [],
        "ticks_flush_rows": 2,
        "ticks_flush_seconds": 0.05,
        "ticks_reconnect_backoff_seconds": 0.01,
        "ticks_idle_timeout_seconds": 5.0,
    }
    return build_settings(database_url=dsn, **{**opts, **kw})


class Server:
    """Plays Kite: records what the client sends and replays scripted messages per connection."""

    def __init__(self, scripts: list[list[bytes | str]]) -> None:
        self.scripts = scripts
        self.received: list[list[Any]] = []
        self.paths: list[str] = []
        self.connections = 0
        self.done = asyncio.Event()

    async def handler(self, ws: Any) -> None:
        idx = self.connections
        self.connections += 1
        self.paths.append(ws.request.path)
        self.received.append([await ws.recv(), await ws.recv()])  # subscribe, then mode
        for msg in self.scripts[idx] if idx < len(self.scripts) else []:
            await ws.send(msg)
        if idx >= len(self.scripts) - 1:
            self.done.set()
            await asyncio.sleep(0.5)  # keep the last connection open until the client stops
        await ws.close()


async def _run(capture: TickCapture, server: Server) -> Any:
    task = asyncio.create_task(capture.run())
    await asyncio.wait_for(server.done.wait(), 10)
    await asyncio.sleep(0.3)  # let the last flush happen
    capture.request_stop()
    return await asyncio.wait_for(task, 10)


async def test_captures_full_ticks_reconnects_and_counts_what_it_refuses(
    pool: Any, db: Any, test_dsn: str
) -> None:
    good_a = build_full_packet(
        TOKEN_A, 141295, ltq=5, volume=1000, oi=0, ltt=1_795_000_000, exch=1_795_000_001,
        bids=((10, 141290, 2),), asks=((7, 141300, 1),),
    )  # fmt: skip
    good_b = build_full_packet(TOKEN_B, 250000, volume=50)
    unknown = build_full_packet(999999, 100)
    too_big = build_full_packet(TOKEN_A, 100, bids=((3_000_000_000, 99, 1),))  # qty beyond int4
    server = Server(
        [
            [
                b"\x00",
                build_message(good_a, good_b, unknown, too_big),
                b"\x00\x01\x00\x03abc",
                "text",
            ],
            [build_message(build_full_packet(TOKEN_A, 141400))],
        ]
    )
    async with websockets.serve(server.handler, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        s = _settings(test_dsn, port)
        capture = TickCapture(pool, Sessions(), s, FrozenClock(NOW))
        st = await _run(capture, server)

    assert (st.status, st.instruments, st.ticks) == ("ok", 2, 3)
    assert (st.rejected, st.unknown_tokens, st.reconnects) == (2, 1, 1)  # too_big + malformed
    # the client subscribed all tokens and asked for Full mode; the token only went in the URL
    assert '"subscribe"' in server.received[0][0]
    assert str(TOKEN_A) in server.received[0][0]
    assert '"full"' in server.received[0][1]
    assert "access_token=secret-token-123" in server.paths[0]

    rows = await db.fetch(
        "SELECT sid, ltp, ltq, volume, b1p, b1q, b1o, a1p, a1q, ltt, ext "
        "FROM app.ticks ORDER BY ltp"
    )
    assert [r["ltp"] for r in rows] == [141295, 141400, 250000]
    first = rows[0]
    assert (first["ltq"], first["volume"], first["b1p"], first["b1q"], first["b1o"]) == (
        5,
        1000,
        141290,
        10,
        2,
    )
    assert (first["a1p"], first["a1q"]) == (141300, 7)
    assert first["ltt"] == datetime.fromtimestamp(1_795_000_000, UTC)
    sids = {r["sid"] for r in rows}
    assert len(sids) == 2  # two instruments, each under its own stable id
    run = await db.fetchrow("SELECT status, ticks, reconnects, rejected FROM app.tick_capture_runs")
    assert (run["status"], run["ticks"], run["reconnects"]) == ("ok", 3, 1)
    assert run["rejected"] == 3  # 2 refused + 1 unknown token


async def test_no_kite_session_means_no_connection(pool: Any, db: Any, test_dsn: str) -> None:
    server = Server([[]])
    async with websockets.serve(server.handler, "127.0.0.1", 0) as srv:
        s = _settings(test_dsn, srv.sockets[0].getsockname()[1])
        st = await TickCapture(pool, Sessions(missing=True), s, FrozenClock(NOW)).run()
    assert st.status == "no_session"
    assert server.connections == 0
    assert await db.fetchval("SELECT count(*) FROM app.tick_capture_runs") == 0


async def test_a_silent_connection_is_dropped_and_reopened(
    pool: Any, db: Any, test_dsn: str
) -> None:
    class Silent(Server):
        async def handler(self, ws: Any) -> None:
            idx = self.connections
            self.connections += 1
            await ws.recv()
            await ws.recv()
            if idx == 1:
                await ws.send(build_message(build_full_packet(TOKEN_A, 123456)))
                self.done.set()
            await asyncio.sleep(2)  # first connection: says nothing at all, not even a heartbeat

    server = Silent([])
    async with websockets.serve(server.handler, "127.0.0.1", 0) as srv:
        s = _settings(test_dsn, srv.sockets[0].getsockname()[1], ticks_idle_timeout_seconds=0.3)
        st = await _run(TickCapture(pool, Sessions(), s, FrozenClock(NOW)), server)
    assert st.reconnects >= 1
    assert await db.fetchval("SELECT ltp FROM app.ticks") == 123456


async def test_rows_stay_buffered_when_the_database_is_unreachable_and_overflow_drops_oldest(
    test_dsn: str,
) -> None:
    class DeadPool:
        def acquire(self) -> Any:
            raise ConnectionError("database unreachable")

    s = build_settings(database_url=test_dsn, kite_api_key="k", ticks_buffer_max_rows=1000)
    cap = TickCapture(DeadPool(), Sessions(), s, FrozenClock(NOW))
    buf = TickBuffer(3)
    for i in range(5):
        buf.add((i,))
    assert (buf.rows, buf.dropped) == ([(2,), (3,), (4,)], 2)  # the oldest went, and was counted
    await cap._flush(buf, cap.stats)
    assert buf.rows == [(2,), (3,), (4,)]  # a failed write keeps everything for the next try


def test_a_tick_that_does_not_fit_its_columns_has_no_row() -> None:
    from app.data.kite_ticks import parse_message

    [ok] = parse_message(build_message(build_full_packet(1, 100, bids=((5, 99, 1),))))
    row = tick_row(ok, 7, NOW)
    assert row is not None
    assert len(row) == len(COLUMNS)
    assert row[1] == 7
    [bad] = parse_message(build_message(build_full_packet(1, 100, volume=1, ltq=3_000_000_000)))
    assert tick_row(bad, 7, NOW) is None
