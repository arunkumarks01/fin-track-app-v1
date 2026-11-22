"""Integration test: paper replay with auto exits."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import pytest

from app.sandbox import store
from app.sandbox.exits import detect_exit, realized_pnl
from app.sandbox.model import Bar, Instruction


@pytest.mark.db
class TestPaperExitFlow:
    """End-to-end: order → fill → next day → auto exit."""

    @pytest.mark.skip(reason="placeholder: needs NSE archive fixture + app_settings wiring")
    async def test_buy_order_exit_on_stop_hit(self, db: Any, app_settings: Any) -> None:
        """
        1. Create account, start day 1
        2. Submit buy order on day 1
        3. Advance to day 2 (auto-fill and check for exit)
        4. Exit order created if SL hit
        """
        async with db.acquire() as conn:
            # Setup: create account starting 2015-01-05 (first trading day in NSE archive)
            acct = await store.create(
                conn,
                name="test_exit",
                start=date(2015, 1, 5),
                mode="learning",
                now=app_settings.clock.now(),
                settings=app_settings,
            )
            account_id = acct["id"]

            # Submit buy order for day 1
            order_1 = Instruction(
                symbol="NSE:RELIANCE",
                side="buy",
                kind="market",
                quantity=10,
                limit_paisa=None,
                trigger_paisa=None,
                risk_stop_paisa=100000,  # ₹1000
                risk_target_paisa=150000,  # ₹1500
            )
            req_id_1 = uuid4()
            order_result_1 = await store.submit(
                conn,
                account_id,
                req_id_1,
                order_1,
                app_settings.clock.now(),
            )
            order_id_1 = order_result_1["id"]
            assert order_result_1["status"] == "pending"

            # Day 2: advance (auto-fill and detect exits)
            # NOTE: In real scenario, day 2 must have NSE archive data for the symbol.
            # For this test, we'd need to mock or provide fixture data.
            # For now, this is a placeholder integration structure.
            await store.advance(
                conn,
                account_id,
                expected=date(2015, 1, 6),
                now=app_settings.clock.now(),
                settings=app_settings,
            )

            # Check: order 1 should have a fill and possibly an exit order
            order_check = await conn.fetchrow(
                "SELECT status, filled, exit_order_id FROM app.paper_orders WHERE id = $1",
                order_id_1,
            )
            assert order_check["filled"] > 0, "Order should have a fill on day 2"
            if order_check["exit_order_id"] is not None:
                exit_ord = await conn.fetchrow(
                    "SELECT status, side FROM app.paper_orders WHERE id = $1",
                    order_check["exit_order_id"],
                )
                assert exit_ord["side"] == "sell", "Exit of a buy is a sell"

    def test_exit_detector_integration_with_bar_model(self) -> None:
        """Exit detector works with Bar fixtures."""
        order = Instruction(
            symbol="NSE:RELIANCE",
            side="buy",
            kind="market",
            quantity=10,
            limit_paisa=None,
            trigger_paisa=None,
            risk_stop_paisa=100000,  # ₹1000
            risk_target_paisa=150000,  # ₹1500
        )

        # Day 1: bar does not hit levels
        bar1 = Bar(open=120000, high=125000, low=115000, close=122000, volume=10000)
        trigger1 = detect_exit(order, bar1, filled_qty=10)
        assert trigger1 is None

        # Day 2: bar hits SL at low
        bar2 = Bar(open=120000, high=125000, low=95000, close=120000, volume=10000)
        trigger2 = detect_exit(order, bar2, filled_qty=10)
        assert trigger2 is not None
        assert trigger2.reason == "stop_hit"

        # Once SL is hit, ideally exit order is placed and order is marked
        # as "has_exit". Subsequent advance() calls skip it.

    def test_pnl_calculation_per_fill(self) -> None:
        """P&L for a filled→exit flow."""
        # Scenario: buy 10 @ 1200, sell 10 @ 1400, fees each direction
        buy_price = 120000  # ₹1200
        sell_price = 140000  # ₹1400
        qty = 10
        fees_buy = 500  # ₹5 brokerage
        fees_sell = 500

        pnl = realized_pnl(buy_price, sell_price, qty, fees_buy, fees_sell)
        # Gross: (1400 - 1200) * 10 = 2000
        # Net: 2000 * 100 - 5 - 5 = 199990 paisa = ₹1999.90
        expected = (sell_price - buy_price) * qty - fees_buy - fees_sell
        assert pnl == expected
