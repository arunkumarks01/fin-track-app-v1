"""Trading-date identity (ARCHITECTURE §5: dedup key = trading identity, not timestamp).

A daily candle's canonical `ts` is 00:00 in the market timezone (IST) of its trading
date. The DB enforces the same rule with a CHECK constraint (migration 0001), so the
same trading day can never land twice under different timestamps.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

# NSE trades on IST. An exchange fact, not a tunable — migration 0001 encodes the same
# zone in the 1d canonical-ts CHECK constraint, so the two must never diverge.
MARKET_TZ = ZoneInfo("Asia/Kolkata")


def canonical_daily_ts(trading_date: date, tz: ZoneInfo) -> datetime:
    """00:00 market-local time on `trading_date`, timezone-aware."""
    return datetime.combine(trading_date, time.min, tzinfo=tz)


def trading_date_of(ts: datetime, tz: ZoneInfo) -> date:
    """The market-local calendar date an aware timestamp falls on."""
    if ts.tzinfo is None:
        raise ValueError("naive datetime — timestamps must be timezone-aware")
    return ts.astimezone(tz).date()
