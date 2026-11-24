"""Single-symbol daily-long execution, with decisions separated from future sessions."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from fractions import Fraction

from app.analytics.indicators import Bar as DailyBar
from app.backtest.replay import Fill, Replay
from app.backtest.signals import Signal
from app.backtest.strategies import DailyLongSpec
from app.core.tradingdate import MARKET_TZ
from app.risk.plans import BPS
from app.sandbox.exits import detect_exit
from app.sandbox.model import Bar, Instruction, adverse_price, fees, sold_cost
from app.sandbox.parameters import PaperParameters
from app.sandbox.trailing import calculate_trailing_sl


def run_daily_long(
    bars: Sequence[DailyBar],
    signals: Sequence[Signal],
    spec: DailyLongSpec,
    symbol: str,
    start_day: date,
    capital_paisa: int,
    quantity: int,
    config: PaperParameters,
) -> Replay:
    """Fixed share requests; no pyramiding, borrowing or final forced liquidation.

    Entry follows a signal at the *previous* final close. Stops/targets are active on
    entry day; stop wins an ambiguous OHLC double touch. Trails earned from today's high
    take effect tomorrow. After N completed holding sessions the time exit is at the next
    open. A partial protective exit latches liquidation of the remainder at following opens.
    The daily volume participation budget is shared by both sides. Market slippage is
    adverse from the known open/trigger and is never improved using a future high/low.
    """
    if capital_paisa <= 0 or not 1 <= quantity <= 1_000_000:
        raise ValueError("Positive capital and quantity up to 1,000,000 required")
    if not 0 < spec.stop_loss_bps < BPS or spec.max_holding_sessions < 1:
        raise ValueError("Invalid strategy risk parameters")
    matched = {
        s.timestamp
        for s in signals
        if (s.strategy_id, s.revision) == (spec.strategy_id, spec.revision)
    }
    cash, held, cost = capital_paisa, 0, 0
    curve, days, fills = [cash], [], []
    pending = False
    expired = 0
    peak, drawdown = cash, Fraction(0)
    entry: Instruction | None = None
    stop, highest, holding = 0, 0, 0
    liquidation: str | None = None
    previous = 0
    for daily in bars:
        if daily.t <= previous:
            raise ValueError("Bars must be strictly chronological and unique")
        previous = daily.t
        day = datetime.fromtimestamp(daily.t, MARKET_TZ).date()
        bar = Bar(daily.open, daily.high, daily.low, daily.close, daily.volume)
        if min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0:
            raise ValueError("Invalid bar price or volume")
        if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
            raise ValueError("Inconsistent OHLC prices")
        if day < start_day:
            continue
        budget = bar.volume * config.volume_bps // BPS
        if pending and not held:
            price = adverse_price(bar.open, "buy", config)
            amount = min(quantity, budget)
            charge = sum(fees(price * amount, "buy", config).values())
            if amount and price * amount + charge <= cash:
                cash -= price * amount + charge
                held, cost = amount, price * amount + charge
                budget -= amount
                fills.append(Fill(day, "buy", amount, price, charge, None, "signal"))
                stop = max(1, price * (BPS - spec.stop_loss_bps) // BPS)
                target = (price * (BPS + spec.target_bps) + BPS - 1) // BPS
                entry = Instruction(
                    symbol=symbol,
                    side="buy",
                    kind="market",
                    quantity=amount,
                    risk_stop_paisa=stop,
                    risk_target_paisa=target,
                )
                highest, holding, liquidation = price, 0, None
            else:
                amount = 0
            if amount < quantity:
                expired += 1
        if held and entry is not None:
            reason, base = liquidation, bar.open
            if reason is None and holding >= spec.max_holding_sessions:
                reason = "max_holding"
            if reason is None:
                trigger = detect_exit(entry, bar, held, stop)
                if trigger is not None:
                    reason, base = trigger.reason, trigger.price
            if reason is not None:
                amount = min(held, budget)
                if amount:
                    price = adverse_price(base, "sell", config)
                    charge = sum(fees(price * amount, "sell", config).values())
                    allocated = sold_cost(cost, held, amount)
                    realized = price * amount - charge - allocated
                    cash += price * amount - charge
                    cost -= allocated
                    held -= amount
                    fills.append(Fill(day, "sell", amount, price, charge, realized, reason))
                liquidation = reason if held else None
            if held:
                holding += 1
                highest = max(highest, bar.high)
                candidate = calculate_trailing_sl(True, highest, 0, spec.trailing_stop_bps, 0)
                stop = max(stop, candidate or stop)
        equity = cash + held * bar.close
        curve.append(equity)
        days.append(day)
        peak = max(peak, equity)
        drawdown = max(drawdown, Fraction(peak - equity, peak))
        pending = daily.t in matched and held == 0
    if not days:
        raise ValueError("No execution sessions in requested range")
    return Replay(
        cash, held, cost, tuple(curve), tuple(days), drawdown, tuple(fills), expired, pending
    )
