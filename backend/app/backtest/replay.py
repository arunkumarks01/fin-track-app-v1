"""A causal single-symbol daily replay using the paper sandbox fill and fee model.

This is an offline building block, not a production backtest runner. The caller supplies
chronological, validated, as-traded bars and instructions observed after each bar closes.
An instruction is eligible only on the next supplied session. Unsupported orders are
rejected rather than modeled optimistically. No database or broker is accessed here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from fractions import Fraction

from app.risk.plans import BPS
from app.sandbox.model import Bar, Instruction, fees, match, sold_cost
from app.sandbox.parameters import PaperParameters


@dataclass(frozen=True)
class Session:
    day: date
    bar: Bar
    # This decision is made at the close, after this session's price data is known.
    instruction: Instruction | None = None


@dataclass(frozen=True)
class Fill:
    day: date
    side: str
    quantity: int
    price_paisa: int
    fees_paisa: int
    realized_paisa: int | None


@dataclass(frozen=True)
class Replay:
    cash_paisa: int
    quantity: int
    cost_paisa: int
    equity_curve: tuple[int, ...]
    max_drawdown: Fraction
    fills: tuple[Fill, ...]
    expired: int
    pending_at_end: bool


def replay(sessions: Sequence[Session], capital_paisa: int, config: PaperParameters) -> Replay:
    """Run close-of-day decisions at the next bar; expire any unfilled day remainder.

    Supports one symbol, long-only market and limit orders. The caller must provide all
    sessions in order; this function cannot determine whether an omitted session is missing.
    Cash includes fees and a sell's realized P&L uses paper's allocated cost basis.
    """
    if capital_paisa <= 0:
        raise ValueError("Starting capital must be positive")
    cash, quantity, cost = capital_paisa, 0, 0
    pending: Instruction | None = None
    symbol: str | None = None
    curve = [cash]
    fills: list[Fill] = []
    expired = 0
    previous: date | None = None
    peak = cash
    drawdown = Fraction(0)
    for session in sessions:
        bar = session.bar
        if previous is not None and session.day <= previous:
            raise ValueError("Sessions must be strictly chronological and unique")
        if min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0:
            raise ValueError("A bar has invalid price or volume")
        if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
            raise ValueError("A bar has inconsistent OHLC prices")
        previous = session.day
        if pending is not None:
            price = match(pending, bar, False, config).price
            amount = min(pending.quantity, bar.volume * config.volume_bps // BPS)
            if pending.side == "sell":
                amount = min(amount, quantity)
            if price is not None and amount > 0:
                total = sum(fees(price * amount, pending.side, config).values())
                # A rejected buy stays rejected: never borrow silently in this simple model.
                if pending.side == "buy" and price * amount + total > cash:
                    amount = 0
                else:
                    realized = None
                    if pending.side == "buy":
                        cash -= price * amount + total
                        quantity += amount
                        cost += price * amount + total
                    else:
                        allocated = sold_cost(cost, quantity, amount)
                        cash += price * amount - total
                        realized = price * amount - total - allocated
                        quantity -= amount
                        cost -= allocated
                    fills.append(Fill(session.day, pending.side, amount, price, total, realized))
            if price is None or amount < pending.quantity:
                expired += 1
        equity = cash + quantity * bar.close
        curve.append(equity)
        peak = max(peak, equity)
        drawdown = max(drawdown, Fraction(peak - equity, peak))
        pending = session.instruction
        if pending is not None:
            if pending.kind not in ("market", "limit"):
                raise ValueError("Only market and limit day orders are supported")
            if symbol is not None and pending.symbol != symbol:
                raise ValueError("Only one symbol is supported")
            symbol = pending.symbol
    # The final close has no next session in this input, so no fill is inferred.
    return Replay(
        cash, quantity, cost, tuple(curve), drawdown, tuple(fills), expired, pending is not None
    )
