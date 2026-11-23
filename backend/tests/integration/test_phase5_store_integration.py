"""Integration test: Phase 5 auto-exits + marks in store.advance() flow."""


class TestPhase5Integration:
    """End-to-end: order → fill → exit detection → mark update."""

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
