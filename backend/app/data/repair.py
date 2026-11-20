"""Repair daily bars that were stored while still provisional (the 1a finality bug).

A 1d bar fetched before D+1 DAILY_BAR_FINAL_NEXT_DAY_IST may carry a pre-official close.
This is the ONLY code path that rewrites a stored bar, and it is fully audited:
  * only bars whose session is final NOW are touched (never another provisional pull);
  * the replacement must pass the same validation gate;
  * per bar, one transaction: old values → quarantine ('superseded_provisional', the
    rollback record) + app.data_repairs (old → new, reason) + the bar updated in place;
  * an unchanged bar is just re-stamped as final (ingested_at), counted as 'confirmed'.
Acceptance is separate: repaired closes must equal NSE's official close (adjustment report).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from app.broker.kite.client import KiteAuthError, KiteError
from app.core.clock import Clock
from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ
from app.data.extract_daily import HistoricalSource, last_final_daily_session
from app.data.validate import validate_daily

log = logging.getLogger(__name__)

REASON = "superseded_provisional"


@dataclass
class RepairReport:
    started_at: datetime
    candidates: int = 0
    repaired: int = 0
    confirmed: int = 0
    failed: dict[str, str] = field(default_factory=dict)
    changes: list[dict[str, Any]] = field(default_factory=list)

    def totals(self) -> dict[str, Any]:
        return {
            "candidates": self.candidates,
            "repaired": self.repaired,
            "confirmed": self.confirmed,
            "errors": len(self.failed),
            "inserted": self.repaired,  # shape shared with the other jobs (Admin page)
            "quarantined": self.repaired,
        }


def _final_at(d: date, settings: Settings) -> datetime:
    return datetime.combine(d + timedelta(days=1), settings.daily_bar_final_next_day_ist, MARKET_TZ)


async def provisional_bars(conn: Any, settings: Settings, now: datetime) -> list[Any]:
    """Stored 1d bars fetched before their session was final, whose session is final now."""
    last_final = last_final_daily_session(now, settings.daily_bar_final_next_day_ist)
    t = settings.daily_bar_final_next_day_ist
    return list(
        await conn.fetch(
            """
            SELECT c.symbol, c.ts, c.open, c.high, c.low, c.close, c.volume, c.oi,
                   c.ingested_at, u.instrument_token, u.kind
            FROM app.ohlcv_candles c JOIN app.universe u USING (symbol)
            WHERE c.timeframe = '1d'
              AND (c.ts AT TIME ZONE 'Asia/Kolkata')::date <= $1
              AND c.ingested_at < ((c.ts AT TIME ZONE 'Asia/Kolkata')::date + 1 + $2::time)
                                  AT TIME ZONE 'Asia/Kolkata'
            ORDER BY c.symbol, c.ts
            """,
            last_final,
            t,
        )
    )


async def repair_provisional_daily(
    pool: Any, source: HistoricalSource, settings: Settings, clock: Clock
) -> RepairReport:
    now = clock.now()
    report = RepairReport(started_at=now)
    async with pool.acquire() as conn:
        rows = await provisional_bars(conn, settings, now)
    report.candidates = len(rows)
    for r in rows:
        d = r["ts"].astimezone(MARKET_TZ).date()
        if now < _final_at(d, settings):  # belt and braces: never pull a provisional bar
            report.failed[r["symbol"]] = f"{d} is not final yet"
            continue
        try:
            records = await source.historical_raw(
                r["instrument_token"],
                "day",
                datetime.combine(d, time.min, MARKET_TZ),
                datetime.combine(d, time(23, 59, 59), MARKET_TZ),
            )
        except KiteAuthError:
            raise  # no session: stop the whole repair (fail closed), nothing half-done
        except KiteError as exc:
            report.failed[r["symbol"]] = f"fetch failed: {exc}"[:300]
            continue
        v = validate_daily(
            r["symbol"],
            records,
            prev=None,  # one bar: gap checks need no neighbour, the price/order gate applies
            large_gap_pct=settings.validation_large_gap_pct,
            missing_weekdays_flag=settings.validation_missing_weekdays_flag,
            is_index=r["kind"] == "index",
            index_max_gap_pct=settings.validation_index_max_gap_pct,
        )
        fresh = [b for b in v.clean if b.ts == r["ts"]]
        if len(fresh) != 1:
            reason = v.rejected[0].reason if v.rejected else "no bar returned"
            report.failed[r["symbol"]] = f"replacement for {d} not usable: {reason}"
            continue
        new = fresh[0].values()
        old = (r["open"], r["high"], r["low"], r["close"], r["volume"], r["oi"])
        now = clock.now()
        keys = ("open", "high", "low", "close", "volume", "oi")
        async with pool.acquire() as conn, conn.transaction():
            if new != old:
                old_json = dict(zip(keys, old, strict=True))
                new_json = dict(zip(keys, new, strict=True))
                await conn.execute(
                    """INSERT INTO app.ohlcv_quarantine
                       (symbol, timeframe, ts, reason, detail, raw, source, quarantined_at)
                       VALUES ($1, '1d', $2, $3, $4, $5::jsonb, 'repair', $6)
                       ON CONFLICT DO NOTHING""",
                    r["symbol"],
                    r["ts"],
                    REASON,
                    f"provisional bar fetched {r['ingested_at'].isoformat()} replaced by the "
                    "final bar",
                    json.dumps(old_json),
                    now,
                )
                await conn.execute(
                    """INSERT INTO app.data_repairs
                       (symbol, timeframe, ts, old_values, new_values, reason, repaired_at)
                       VALUES ($1, '1d', $2, $3::jsonb, $4::jsonb, $5, $6)""",
                    r["symbol"],
                    r["ts"],
                    json.dumps(old_json),
                    json.dumps(new_json),
                    REASON,
                    now,
                )
                report.repaired += 1
                report.changes.append(
                    {"symbol": r["symbol"], "date": d.isoformat(), "old": old_json, "new": new_json}
                )
            else:
                report.confirmed += 1
            await conn.execute(
                """UPDATE app.ohlcv_candles
                   SET open = $3, high = $4, low = $5, close = $6, volume = $7, oi = $8,
                       ingested_at = $9
                   WHERE symbol = $1 AND timeframe = '1d' AND ts = $2""",
                r["symbol"],
                r["ts"],
                *new,
                now,
            )
    log.info("provisional repair: %s", report.totals())
    return report
