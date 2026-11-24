"""Deterministic performance measures for an already-causal replay."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from app.backtest.replay import Replay


@dataclass(frozen=True)
class Performance:
    """Summary that deliberately distinguishes closed outcomes from open equity."""

    starting_capital_paisa: int
    ending_equity_paisa: int
    net_paisa: int
    return_fraction: Fraction
    max_drawdown: Fraction
    closed_trades: int
    wins: int
    losses: int
    gross_profit_paisa: int
    gross_loss_paisa: int
    profit_factor: Fraction | None


def performance(replay: Replay, starting_capital_paisa: int) -> Performance:
    """Summarise fills without treating an open position as a completed trade.

    ``Replay.equity_curve`` is marked to each supplied final close, so its final point is
    the only valid ending equity.  A profit factor is unavailable when there are no losing
    closed trades; reporting infinity would overstate a tiny or incomplete sample.
    """
    if starting_capital_paisa <= 0:
        raise ValueError("Starting capital must be positive")
    outcomes: list[int] = []
    held, realized = 0, 0
    for fill in replay.fills:
        if fill.side == "buy":
            held += fill.quantity
        else:
            held -= fill.quantity
            realized += fill.realized_paisa or 0
            if held == 0:
                outcomes.append(realized)
                realized = 0
    gross_profit = sum(value for value in outcomes if value > 0)
    gross_loss = -sum(value for value in outcomes if value < 0)
    ending = replay.equity_curve[-1]
    return Performance(
        starting_capital_paisa=starting_capital_paisa,
        ending_equity_paisa=ending,
        net_paisa=ending - starting_capital_paisa,
        return_fraction=Fraction(ending - starting_capital_paisa, starting_capital_paisa),
        max_drawdown=replay.max_drawdown,
        closed_trades=len(outcomes),
        wins=sum(value > 0 for value in outcomes),
        losses=sum(value < 0 for value in outcomes),
        gross_profit_paisa=gross_profit,
        gross_loss_paisa=gross_loss,
        profit_factor=Fraction(gross_profit, gross_loss) if gross_loss else None,
    )
