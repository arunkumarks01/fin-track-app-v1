"""Detect Kite re-adjusting history after we stored it (Phase 1c drift re-check).

Every catch-up re-fetches the newest stored session along with the new ones. If the stored
bars come back scaled by ONE consistent ratio (all of open/high/low/close, within the paisa
tolerance), Kite has applied a new corporate action to its history: that is recorded in
app.adjustment_drift — never written over the stored bars — and the adjusted-as-of-now view
multiplies bars on/before ref_date by it. Anything not proportional stays a
'conflicts_with_stored' quarantine, as before. Each completed check is recorded so a symbol
whose check did not run is visibly "may be stale".
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any


def proportional_ratio(
    pairs: Sequence[tuple[Sequence[Any], Sequence[Any]]], tol_paisa: float
) -> float | None:
    """The single ratio fresh/stored that explains EVERY differing (stored, fresh) OHLC pair,
    or None when they are not all scaled alike. Only prices (first four values) count:
    volume has its own factor."""
    if not pairs:
        return None
    s0, f0 = pairs[0]
    if s0[3] <= 0:
        return None
    r = float(f0[3]) / float(s0[3])
    for stored, fresh in pairs:
        for i in range(4):
            if abs(fresh[i] - stored[i] * r) > tol_paisa:
                return None
    return r


async def record_drift(
    conn: Any, symbol: str, timeframe: str, ref_date: date, ratio: float, now: datetime
) -> None:
    await conn.execute(
        """INSERT INTO app.adjustment_drift (symbol, timeframe, ref_date, ratio, detected_at)
           VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING""",
        symbol,
        timeframe,
        ref_date,
        format(ratio, ".12g"),
        now,
    )


async def record_check(
    conn: Any, symbol: str, timeframe: str, checked_on: date, now: datetime
) -> None:
    await conn.execute(
        """INSERT INTO app.adjustment_checks (symbol, timeframe, checked_on, checked_at)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (symbol, timeframe) DO UPDATE
           SET checked_on = GREATEST(app.adjustment_checks.checked_on, EXCLUDED.checked_on),
               checked_at = EXCLUDED.checked_at""",
        symbol,
        timeframe,
        checked_on,
        now,
    )
