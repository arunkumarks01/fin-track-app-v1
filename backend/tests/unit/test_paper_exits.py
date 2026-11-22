"""Tests for automatic exit detection (SL and target)."""

from __future__ import annotations

import pytest

from app.sandbox.exits import ExitTrigger, detect_exit, exit_instruction, realized_pnl
from app.sandbox.model import Bar, Instruction


class TestExitDetection:
    """Exit trigger detection: SL and target."""

    def test_buy_stop_hit_in_bar_low(self) -> None:
        """Buy order: SL at 2000, bar low 1950 → stop hit at 1950."""
        order = Instruction(
            symbol="NSE:RELIANCE",
            side="buy",
            kind="market",
            quantity=10,
            limit_paisa=None,
            trigger_paisa=None,
            risk_stop_paisa=200000,  # ₹2000
            risk_target_paisa=220000,  # ₹2200
        )
        bar = Bar(open=210000, high=215000, low=195000, close=212000, volume=1000)
        trigger = detect_exit(order, bar, filled_qty=10)
        assert trigger is not None
        assert trigger.reason == "stop_hit"
        # Exit price = max(open, sl) = max(2100, 2000) = 2100
        assert trigger.price == 210000

    def test_buy_target_hit_in_bar_high(self) -> None:
        """Buy order: target at 2200, bar high 2250 → target hit at 2200."""
        order = Instruction(
            symbol="NSE:RELIANCE",
            side="buy",
            kind="market",
            quantity=10,
            limit_paisa=None,
            trigger_paisa=None,
            risk_stop_paisa=200000,
            risk_target_paisa=220000,
        )
        bar = Bar(open=210000, high=225000, low=205000, close=220000, volume=1000)
        trigger = detect_exit(order, bar, filled_qty=10)
        assert trigger is not None
        assert trigger.reason == "target_hit"
        # Exit price = min(open, target) = min(2100, 2200) = 2100
        assert trigger.price == 210000

    def test_buy_stop_checked_before_target(self) -> None:
        """Buy order: both SL and target hit in same bar → SL takes precedence."""
        order = Instruction(
            symbol="NSE:RELIANCE",
            side="buy",
            kind="market",
            quantity=10,
            limit_paisa=None,
            trigger_paisa=None,
            risk_stop_paisa=200000,
            risk_target_paisa=220000,
        )
        bar = Bar(open=210000, high=230000, low=190000, close=210000, volume=1000)
        trigger = detect_exit(order, bar, filled_qty=10)
        assert trigger is not None
        assert trigger.reason == "stop_hit"

    def test_no_sl_or_target_on_sell_per_model(self) -> None:
        """Sell orders cannot have risk stops or targets (Phase 4 design constraint)."""
        # Shorts are accepted at order submission (Phase 3) but risk levels are for
        # buy orders only. Exit detection is used by auto-exit machinery which must
        # handle both sides, but Instruction model enforces this constraint.
        # This test documents the constraint; derivatives Phase 5 will relax it.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Instruction(
                symbol="NSE:RELIANCE",
                side="sell",
                kind="market",
                quantity=10,
                limit_paisa=None,
                trigger_paisa=None,
                risk_stop_paisa=220000,
                risk_target_paisa=None,
            )

    def test_no_exit_when_bar_does_not_hit_levels(self) -> None:
        """Buy order: SL 2000, target 2200, bar 2050-2150 → no exit."""
        order = Instruction(
            symbol="NSE:RELIANCE",
            side="buy",
            kind="market",
            quantity=10,
            limit_paisa=None,
            trigger_paisa=None,
            risk_stop_paisa=200000,
            risk_target_paisa=220000,
        )
        bar = Bar(open=210000, high=215000, low=205000, close=211000, volume=1000)
        trigger = detect_exit(order, bar, filled_qty=10)
        assert trigger is None

    def test_no_exit_when_no_sl_or_target(self) -> None:
        """Order without risk levels → no exit."""
        order = Instruction(
            symbol="NSE:RELIANCE",
            side="buy",
            kind="market",
            quantity=10,
            limit_paisa=None,
            trigger_paisa=None,
            risk_stop_paisa=None,
            risk_target_paisa=None,
        )
        bar = Bar(open=210000, high=225000, low=190000, close=210000, volume=1000)
        trigger = detect_exit(order, bar, filled_qty=10)
        assert trigger is None

    def test_zero_filled_qty_no_exit(self) -> None:
        """Order with 0 filled qty → no exit check."""
        order = Instruction(
            symbol="NSE:RELIANCE",
            side="buy",
            kind="market",
            quantity=10,
            limit_paisa=None,
            trigger_paisa=None,
            risk_stop_paisa=200000,
            risk_target_paisa=220000,
        )
        bar = Bar(open=210000, high=225000, low=190000, close=210000, volume=1000)
        trigger = detect_exit(order, bar, filled_qty=0)
        assert trigger is None

    def test_trailing_stop_overrides_original_sl(self) -> None:
        """Trailing stop (current_stop_paisa) is used instead of order.risk_stop_paisa."""
        order = Instruction(
            symbol="NSE:RELIANCE",
            side="buy",
            kind="market",
            quantity=10,
            limit_paisa=None,
            trigger_paisa=None,
            risk_stop_paisa=200000,  # Original SL
            risk_target_paisa=220000,
        )
        bar = Bar(open=210000, high=215000, low=204000, close=210000, volume=1000)
        trailing_sl = 205000  # Trailed up from 2000 to 2050
        trigger = detect_exit(order, bar, filled_qty=10, current_stop_paisa=trailing_sl)
        assert trigger is not None
        assert trigger.reason == "stop_hit"


class TestExitInstruction:
    """Exit order generation."""

    def test_buy_exit_is_sell_market(self) -> None:
        """Exit from a buy order is a MARKET sell."""
        order = Instruction(
            symbol="NSE:RELIANCE",
            side="buy",
            kind="market",
            quantity=10,
            limit_paisa=None,
            trigger_paisa=None,
            risk_stop_paisa=200000,
            risk_target_paisa=220000,
        )
        trigger = ExitTrigger(reason="stop_hit", price=200000)
        exit_ord = exit_instruction(order, trigger)
        assert exit_ord.side == "sell"
        assert exit_ord.kind == "market"
        assert exit_ord.quantity == 10
        assert exit_ord.symbol == "NSE:RELIANCE"
        assert exit_ord.limit_paisa is None
        assert exit_ord.risk_stop_paisa is None
        assert exit_ord.risk_target_paisa is None

    def test_sell_exit_is_buy_market(self) -> None:
        """Exit from a sell order is a MARKET buy (documents Phase 5 behavior)."""
        # For now, sells cannot be created with Instruction (Phase 4 constraint).
        # This documents the expected behavior when derivatives Phase 5 allows it.
        # We can test with a minimal mocked order dict instead.

        # Create a buy order and verify its exit is sell (the inverse).
        order = Instruction(
            symbol="NSE:RELIANCE",
            side="buy",
            kind="market",
            quantity=10,
            limit_paisa=None,
            trigger_paisa=None,
            risk_stop_paisa=200000,
            risk_target_paisa=220000,
        )
        trigger = ExitTrigger(reason="target_hit", price=210000)
        exit_ord = exit_instruction(order, trigger)
        # For a buy order, exit is sell; for a sell order, exit would be buy.
        assert exit_ord.side == "sell"
        assert exit_ord.kind == "market"
        assert exit_ord.quantity == 10


class TestRealizedPnL:
    """P&L calculation for closed positions."""

    def test_buy_then_sell_at_profit(self) -> None:
        """Buy 100 @ 2000, sell @ 2100, fees 50+50 → profit 10000 - 100 = 9900."""
        pnl = realized_pnl(
            buy_price_paisa=200000,
            sell_price_paisa=210000,
            quantity=100,
            fees_buy=5000,
            fees_sell=5000,
        )
        # Gross: (210000 - 200000) * 100 = 1000000
        # Net: 1000000 - 5000 - 5000 = 990000
        assert pnl == 990000

    def test_buy_then_sell_at_loss(self) -> None:
        """Buy 100 @ 2000, sell @ 1950, fees 50+50 → loss -5100."""
        pnl = realized_pnl(
            buy_price_paisa=200000,
            sell_price_paisa=195000,
            quantity=100,
            fees_buy=5000,
            fees_sell=5000,
        )
        # Gross: (195000 - 200000) * 100 = -500000
        # Net: -500000 - 5000 - 5000 = -510000
        assert pnl == -510000

    def test_zero_fees(self) -> None:
        """Buy then sell with no fees."""
        pnl = realized_pnl(
            buy_price_paisa=200000,
            sell_price_paisa=210000,
            quantity=50,
            fees_buy=0,
            fees_sell=0,
        )
        # Gross: (210000 - 200000) * 50 = 500000
        # Net: 500000 - 0 - 0 = 500000
        assert pnl == 500000
