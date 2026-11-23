"""Kite binary tick packets: the documented layout, and strict refusal of anything else."""

from __future__ import annotations

import struct
from datetime import UTC, datetime

import pytest

from app.data.kite_ticks import (
    TickParseError,
    build_full_packet,
    build_message,
    parse_message,
)


def test_full_packet_round_trips_every_field_and_the_ten_depth_levels() -> None:
    pkt = build_full_packet(
        408065,
        141295,
        ltq=5,
        atp=141247,
        volume=7360198,
        buy_qty=1234,
        sell_qty=5191,
        oi=900,
        ltt=1_794_000_000,
        exch=1_794_000_005,
        bids=((10, 141290, 2), (20, 141285, 3)),
        asks=((5, 141300, 1),),
    )
    assert len(pkt) == 184
    [t] = parse_message(build_message(pkt))
    assert (t.token, t.ltp, t.kind, t.last_qty, t.avg_price, t.volume) == (
        408065, 141295, "full", 5, 141247, 7360198,
    )  # fmt: skip
    assert (t.buy_qty, t.sell_qty, t.oi) == (1234, 5191, 900)
    assert t.last_trade_time == datetime.fromtimestamp(1_794_000_000, UTC)
    assert t.exchange_time == datetime.fromtimestamp(1_794_000_005, UTC)
    assert [(d.qty, d.price, d.orders) for d in t.bids[:2]] == [(10, 141290, 2), (20, 141285, 3)]
    assert (t.asks[0].qty, t.asks[0].price, t.asks[0].orders) == (5, 141300, 1)
    assert len(t.bids) == len(t.asks) == 5  # five levels a side, empty ones are zeros


def test_a_message_can_carry_several_packets_of_different_kinds() -> None:
    index = struct.pack(">IIIIIIII", 256265, 2_580_000, 1, 1, 1, 1, 0, 1_794_000_000)
    ltp = struct.pack(">II", 99, 12345)
    msgs = parse_message(build_message(build_full_packet(1, 100), index, ltp))
    assert [(t.token, t.kind) for t in msgs] == [(1, "full"), (256265, "index"), (99, "ltp")]
    assert msgs[1].ltp == 2_580_000


def test_heartbeats_are_not_ticks_and_malformed_messages_are_refused() -> None:
    assert parse_message(b"\x00") == []
    assert parse_message(b"") == []
    good = build_message(build_full_packet(1, 100))
    with pytest.raises(TickParseError, match="past the end"):
        parse_message(good[:-5])
    with pytest.raises(TickParseError, match="trailing"):
        parse_message(good + b"\x00\x00")
    with pytest.raises(TickParseError, match="unknown packet length"):
        parse_message(build_message(b"\x00" * 50))
    with pytest.raises(TickParseError, match="header"):
        parse_message(b"\x00\x02\x00\x08" + b"\x00" * 8)  # promises two packets, has one
