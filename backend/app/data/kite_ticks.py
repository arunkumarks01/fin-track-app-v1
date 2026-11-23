"""Kite WebSocket binary tick packets (slice 1d, DEC-1D-TICKS): a pure, strict parser.

A WebSocket binary message is: u16 packet count, then per packet u16 length + that many bytes.
All integers are big-endian; prices are integers in paisa (Kite sends rupees x 100 for these
segments, so they are stored as-is — never through floats). Packet kinds by length:

   8  LTP only         token, last price
  28  index quote      token, ltp, high, low, open, close, change
  32  index full       ... + exchange timestamp
  44  tradable quote   token, ltp, last qty, avg price, volume, total buy qty, total sell qty,
                       day open, high, low, close
 184  tradable full    the 44 bytes + last trade time, OI, OI day high, OI day low,
                       exchange timestamp, then 10 depth levels (5 buy, 5 sell):
                       qty u32, price u32, orders u16, 2 pad bytes

Anything else (other lengths, a length that overruns the message, trailing bytes) is rejected
and counted — never guessed. A 1-byte message is Kite's heartbeat and is not a tick.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import UTC, datetime

INT4_MAX = 2_147_483_647
_HEAD = struct.Struct(">IIIIIIIIIIIIIIII")  # the first 64 bytes of a tradable full packet
_DEPTH = struct.Struct(">IIH2x")  # qty, price, orders, 2 pad bytes (12 bytes)
_INDEX = struct.Struct(">IIIIIII")  # 28 bytes
_INDEX_FULL = struct.Struct(">IIIIIIII")  # 32 bytes
_QUOTE = struct.Struct(">IIIIIIIIIII")  # 44 bytes
_LTP = struct.Struct(">II")  # 8 bytes


class TickParseError(ValueError):
    """The message is not a well-formed Kite tick message."""


@dataclass(frozen=True)
class DepthLevel:
    qty: int
    price: int  # paisa
    orders: int


@dataclass(frozen=True)
class Tick:
    token: int
    ltp: int  # paisa
    kind: str  # 'ltp' | 'index' | 'quote' | 'full'
    last_qty: int | None = None
    avg_price: int | None = None
    volume: int | None = None
    buy_qty: int | None = None
    sell_qty: int | None = None
    oi: int | None = None
    last_trade_time: datetime | None = None
    exchange_time: datetime | None = None
    bids: tuple[DepthLevel, ...] = ()
    asks: tuple[DepthLevel, ...] = ()


def _when(seconds: int) -> datetime | None:
    """Exchange timestamps are unix seconds; 0 means 'not set'."""
    return datetime.fromtimestamp(seconds, UTC) if seconds else None


def _packet(pkt: bytes) -> Tick:
    n = len(pkt)
    if n == 8:
        token, ltp = _LTP.unpack(pkt)
        return Tick(token, ltp, "ltp")
    if n in (28, 32):
        if n == 28:
            token, ltp, _h, _lo, _o, _c, _chg = _INDEX.unpack(pkt)
            return Tick(token, ltp, "index")
        token, ltp, *_rest, ex = _INDEX_FULL.unpack(pkt)
        return Tick(token, ltp, "index", exchange_time=_when(ex))
    if n == 44:
        token, ltp, ltq, atp, vol, bq, sq, *_ohlc = _QUOTE.unpack(pkt)
        return Tick(token, ltp, "quote", ltq, atp, vol, bq, sq)
    if n == 184:
        v = _HEAD.unpack(pkt[:64])
        levels = [_DEPTH.unpack(pkt[64 + i * 12 : 76 + i * 12]) for i in range(10)]
        depth = [DepthLevel(q, p, o) for q, p, o in levels]
        return Tick(
            token=v[0],
            ltp=v[1],
            kind="full",
            last_qty=v[2],
            avg_price=v[3],
            volume=v[4],
            buy_qty=v[5],
            sell_qty=v[6],
            oi=v[12],
            last_trade_time=_when(v[11]),
            exchange_time=_when(v[15]),
            bids=tuple(depth[:5]),
            asks=tuple(depth[5:]),
        )
    raise TickParseError(f"unknown packet length {n}")


def parse_message(msg: bytes) -> list[Tick]:
    """All ticks in one binary WebSocket message ([] for a heartbeat)."""
    if len(msg) < 2:
        return []
    (count,) = struct.unpack(">H", msg[:2])
    off = 2
    out: list[Tick] = []
    for _ in range(count):
        if off + 2 > len(msg):
            raise TickParseError("message ends inside a packet header")
        (ln,) = struct.unpack(">H", msg[off : off + 2])
        off += 2
        if off + ln > len(msg):
            raise TickParseError("packet runs past the end of the message")
        out.append(_packet(msg[off : off + ln]))
        off += ln
    if off != len(msg):
        raise TickParseError(f"{len(msg) - off} trailing bytes after the last packet")
    return out


def build_full_packet(
    token: int,
    ltp: int,
    *,
    ltq: int = 1,
    atp: int | None = None,
    volume: int = 0,
    buy_qty: int = 0,
    sell_qty: int = 0,
    oi: int = 0,
    ltt: int = 0,
    exch: int = 0,
    bids: tuple[tuple[int, int, int], ...] = (),
    asks: tuple[tuple[int, int, int], ...] = (),
) -> bytes:
    """Build a 184-byte full packet in Kite's documented layout (tests and replay tools)."""
    head = _HEAD.pack(
        token, ltp, ltq, atp if atp is not None else ltp, volume, buy_qty, sell_qty,
        ltp, ltp, ltp, ltp, ltt, oi, 0, 0, exch,
    )  # fmt: skip
    pad = ((0, 0, 0),) * 5
    levels = (tuple(bids) + pad)[:5] + (tuple(asks) + pad)[:5]
    return head + b"".join(_DEPTH.pack(q, p, o) for q, p, o in levels)


def build_message(*packets: bytes) -> bytes:
    return struct.pack(">H", len(packets)) + b"".join(
        struct.pack(">H", len(p)) + p for p in packets
    )
