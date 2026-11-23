from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.data.tick_export import _full_hash, _table
from app.data.ticks import COLUMNS


def _row() -> dict:
    row = dict.fromkeys(COLUMNS)
    row.update(ts=datetime(2026, 1, 1, tzinfo=UTC), sid=1, ltp=123)
    return row


@pytest.mark.parametrize("column", COLUMNS)
def test_full_hash_detects_change_to_every_field(column: str) -> None:
    original = _row()
    changed = original.copy()
    changed[column] = (
        datetime(2026, 1, 2, tzinfo=UTC)
        if column in ("ts", "ltt", "ext")
        else (2 if original[column] != 2 else 3)
    )
    assert _full_hash(_table([original])) != _full_hash(_table([changed]))


def test_full_hash_is_order_independent_and_duplicate_sensitive() -> None:
    a = _row()
    b = {**a, "ltp": 456}
    assert _full_hash(_table([a, b])) == _full_hash(_table([b, a]))
    assert _full_hash(_table([a, b])) != _full_hash(_table([a, a]))
