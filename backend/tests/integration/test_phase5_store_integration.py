"""Integration test: Phase 5 auto-exits + marks in store.advance() flow."""

from datetime import date
from typing import Any
from uuid import uuid4

import pytest

from app.sandbox import store
from app.sandbox.model import Instruction


@pytest.mark.db
class TestPhase5Integration:
    """End-to-end: order → fill → exit detection → mark update."""

    @pytest.mark.skip(reason="placeholder: needs NSE archive fixture + app_settings wiring")
    async def test_auto_exit_on_sl_hit(self, db: Any, app_settings: Any) -> None:
        """
        1. Create account
        2. Submit buy order with SL/target
        3. Advance: fill + detect exit (SL hit)
        4. Auto-exit order created
        5. Marks updated with MTM
        """
        async with db.acquire() as conn:
            # Setup
            acct = await store.create(
                conn,
                name="phase5_test",
                start=date(2015, 1, 5),
                mode="learning",
                now=app_settings.clock.now(),
                settings=app_settings,
            )
            account_id = acct["id"]

            # Order: buy 10 @ market, SL ₹1900, target ₹2100
            order_1 = Instruction(
                symbol="NSE:RELIANCE",
                side="buy",
                kind="market",
                quantity=10,
                limit_paisa=None,
                trigger_paisa=None,
                risk_stop_paisa=190000,
                risk_target_paisa=210000,
            )
            req_1 = uuid4()
            ord_result = await store.submit(
                conn, account_id, req_1, order_1, app_settings.clock.now()
            )
            assert ord_result["status"] == "pending"

            # Advance: order fills + exit detection
            overview_before = await store.overview(conn, account_id)
            assert len(overview_before["positions"]) == 0

            # This would need real NSE archive data to fully work
            # For now, test the structure is wired (no DB error)
            # Real test requires Phase 1 data fixture

    def test_mark_calculation(self) -> None:
        """Mark price + MTM calculation works standalone."""
        from app.sandbox.marks import calculate_mtm

        # Buy 10 @ 2000, mark @ 2050 → profit ₹500
        mark = calculate_mtm(
            mark_paisa=205000,
            cost_basis_paisa_per_share=200000,
            quantity=10,
        )
        assert mark == 50000  # (2050-2000)*10 = 500 rupees

    def test_trailing_stop_progression(self) -> None:
        """Trailing stop updates as price moves."""
        from app.sandbox.trailing import calculate_trailing_sl, should_update_trailing_sl

        # Buy @ 2000, price rises to 2100, 1% trail → SL moves to 2079
        new_sl = calculate_trailing_sl(
            buy_order=True,
            highest_paisa_seen=210000,
            lowest_paisa_seen=0,
            trailing_stop_bps=100,
            trailing_stop_paisa_min=0,
        )
        assert new_sl == 207900

        # Check if update should happen (old SL 2000 → new SL 2079 = move up)
        should_update = should_update_trailing_sl(
            current_stop_paisa=200000,
            new_stop_paisa=207900,
            buy_order=True,
        )
        assert should_update is True
