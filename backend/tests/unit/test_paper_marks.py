"""Tests for live marks and MTM calculation."""

from app.sandbox.marks import calculate_mtm, portfolio_mtm, stale_mark_age_seconds


class TestCalculateMTM:
    """Mark-to-market P&L for a single position."""

    def test_long_position_at_profit(self) -> None:
        """Buy 100 @ 2000, mark @ 2100 → ₹10,000 unrealized gain."""
        mtm = calculate_mtm(
            mark_paisa=210000,  # ₹2100
            cost_basis_paisa_per_share=200000,  # ₹2000
            quantity=100,
        )
        # (2100 - 2000) * 100 = 10000 rupees = 1000000 paisa
        assert mtm == 1000000

    def test_long_position_at_loss(self) -> None:
        """Buy 100 @ 2000, mark @ 1950 → ₹-5,000 unrealized loss."""
        mtm = calculate_mtm(
            mark_paisa=195000,  # ₹1950
            cost_basis_paisa_per_share=200000,  # ₹2000
            quantity=100,
        )
        # (1950 - 2000) * 100 = -5000 rupees = -500000 paisa
        assert mtm == -500000

    def test_break_even(self) -> None:
        """Buy 100 @ 2000, mark @ 2000 → ₹0."""
        mtm = calculate_mtm(
            mark_paisa=200000,
            cost_basis_paisa_per_share=200000,
            quantity=100,
        )
        assert mtm == 0

    def test_fractional_share(self) -> None:
        """Buy 1 @ 2000.50, mark @ 2001.00 → ₹0.50 gain."""
        mtm = calculate_mtm(
            mark_paisa=200150,  # ₹2001.50
            cost_basis_paisa_per_share=200050,  # ₹2000.50
            quantity=1,
        )
        # 100 paisa = ₹1
        assert mtm == 100


class TestPortfolioMTM:
    """Sum MTM across multiple positions."""

    def test_multiple_positions_mixed_pnl(self) -> None:
        """3 positions: +₹1000, -₹500, +₹200 → +₹700 total."""
        positions = {
            "NSE:RELIANCE": 100000,  # +₹1000
            "NSE:INFY": -50000,  # -₹500
            "NSE:TCS": 20000,  # +₹200
        }
        portfolio = portfolio_mtm(positions)
        assert portfolio == 70000

    def test_all_losses(self) -> None:
        """All positions losing → negative total."""
        positions = {
            "NSE:RELIANCE": -100000,
            "NSE:INFY": -50000,
        }
        portfolio = portfolio_mtm(positions)
        assert portfolio == -150000

    def test_empty_portfolio(self) -> None:
        """No positions → zero MTM."""
        portfolio = portfolio_mtm({})
        assert portfolio == 0


class TestStaleMark:
    """Detect old marks."""

    def test_recent_mark(self) -> None:
        """Mark from 10 seconds ago → not stale."""
        age = stale_mark_age_seconds(
            marked_at_unix=1000.0,
            now_unix=1010.0,
        )
        assert age == 10.0

    def test_one_day_old_mark(self) -> None:
        """Mark from 24+ hours ago → stale."""
        age = stale_mark_age_seconds(
            marked_at_unix=1000.0,
            now_unix=1000.0 + 86400 + 1,  # +1 day + 1 second
        )
        assert age > 86400

    def test_future_mark(self) -> None:
        """Mark timestamp in the future (bug) → negative age."""
        age = stale_mark_age_seconds(
            marked_at_unix=2000.0,
            now_unix=1000.0,
        )
        assert age < 0
