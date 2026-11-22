"""Tests for trailing stop management."""


from app.sandbox.trailing import (
    calculate_trailing_sl,
    should_update_trailing_sl,
    update_extremes,
)


class TestCalculateTrailingSL:
    """Trailing SL calculation for buy and sell orders."""

    def test_buy_order_trail_upward_on_price_rise(self) -> None:
        """Buy @ 2000, price rises to 2100, trail 1% → SL = 2079."""
        new_sl = calculate_trailing_sl(
            buy_order=True,
            highest_paisa_seen=210000,  # ₹2100
            lowest_paisa_seen=0,
            trailing_stop_bps=100,  # 1%
            trailing_stop_paisa_min=0,
        )
        # Trail: 210000 * 100 / 10000 = 2100 paisa
        # New SL: 210000 - 2100 = 207900 (₹2079)
        assert new_sl == 207900

    def test_buy_order_respects_minimum_trail_distance(self) -> None:
        """Buy @ 2000, price rises to 2001, min trail ₹5 → SL = 1996."""
        new_sl = calculate_trailing_sl(
            buy_order=True,
            highest_paisa_seen=200100,  # ₹2001
            lowest_paisa_seen=0,
            trailing_stop_bps=1,  # 0.01% would be tiny
            trailing_stop_paisa_min=500,  # Minimum ₹5
        )
        # Min trail applies: 500 paisa
        # New SL: 200100 - 500 = 199600 (₹1996)
        assert new_sl == 199600

    def test_sell_order_trail_downward_on_price_fall(self) -> None:
        """Sell @ 2000, price falls to 1900, trail 1% → SL = 1919."""
        new_sl = calculate_trailing_sl(
            buy_order=False,
            highest_paisa_seen=0,
            lowest_paisa_seen=190000,  # ₹1900
            trailing_stop_bps=100,  # 1%
            trailing_stop_paisa_min=0,
        )
        # Trail: 190000 * 100 / 10000 = 1900 paisa
        # New SL: 190000 + 1900 = 191900 (₹1919)
        assert new_sl == 191900

    def test_disabled_trailing_returns_none(self) -> None:
        """Trailing disabled (0 bps) → returns None."""
        new_sl = calculate_trailing_sl(
            buy_order=True,
            highest_paisa_seen=210000,
            lowest_paisa_seen=0,
            trailing_stop_bps=0,
            trailing_stop_paisa_min=0,
        )
        assert new_sl is None

    def test_no_extreme_seen_returns_none(self) -> None:
        """Buy order, no highest price yet → None."""
        new_sl = calculate_trailing_sl(
            buy_order=True,
            highest_paisa_seen=0,
            lowest_paisa_seen=0,
            trailing_stop_bps=100,
            trailing_stop_paisa_min=0,
        )
        assert new_sl is None


class TestShouldUpdateTrailingSL:
    """Decide if a trailing SL update should be applied."""

    def test_buy_order_update_if_new_sl_higher(self) -> None:
        """Buy order: current SL 2080, new SL 2090 → update (more profitable)."""
        should_update = should_update_trailing_sl(
            current_stop_paisa=208000,
            new_stop_paisa=209000,
            buy_order=True,
        )
        assert should_update is True

    def test_buy_order_no_update_if_new_sl_lower(self) -> None:
        """Buy order: current SL 2090, new SL 2080 → no update (less profitable)."""
        should_update = should_update_trailing_sl(
            current_stop_paisa=209000,
            new_stop_paisa=208000,
            buy_order=True,
        )
        assert should_update is False

    def test_sell_order_update_if_new_sl_lower(self) -> None:
        """Sell order: current SL 1920, new SL 1910 → update (more profitable)."""
        should_update = should_update_trailing_sl(
            current_stop_paisa=192000,
            new_stop_paisa=191000,
            buy_order=False,
        )
        assert should_update is True

    def test_sell_order_no_update_if_new_sl_higher(self) -> None:
        """Sell order: current SL 1910, new SL 1920 → no update (less profitable)."""
        should_update = should_update_trailing_sl(
            current_stop_paisa=191000,
            new_stop_paisa=192000,
            buy_order=False,
        )
        assert should_update is False

    def test_none_values_return_false(self) -> None:
        """Missing SL values → no update."""
        assert (
            should_update_trailing_sl(
                current_stop_paisa=None, new_stop_paisa=209000, buy_order=True
            )
            is False
        )
        assert (
            should_update_trailing_sl(
                current_stop_paisa=208000, new_stop_paisa=None, buy_order=True
            )
            is False
        )


class TestUpdateExtremes:
    """Track highest/lowest prices for trailing SL."""

    def test_buy_order_track_highest(self) -> None:
        """Buy order: bar moves 2100→2110, highest should be 2110."""
        highest, lowest = update_extremes(
            buy_order=True,
            highest_paisa_seen=210000,  # Previous high ₹2100
            lowest_paisa_seen=None,
            bar_high=211000,  # Today's high ₹2110
            bar_low=209000,  # Today's low ₹2090
        )
        assert highest == 211000
        assert lowest == 0

    def test_buy_order_no_update_if_bar_lower(self) -> None:
        """Buy order: highest was 2110, bar goes to 2100→2105 → highest stays 2110."""
        highest, _lowest = update_extremes(
            lowest_paisa_seen=None,
            bar_high=210500,
            bar_low=210000,
        )
        assert highest == 211000

    def test_sell_order_track_lowest(self) -> None:
        """Sell order: bar moves 1900→1890, lowest should be 1890."""
        highest, lowest = update_extremes(
            buy_order=False,
            highest_paisa_seen=None,
            lowest_paisa_seen=190000,  # Previous low ₹1900
            bar_high=191000,  # Today's high ₹1910
            bar_low=189000,  # Today's low ₹1890
        )
        assert highest == 0
        assert lowest == 189000

    def test_sell_order_no_update_if_bar_higher(self) -> None:
        """Sell order: lowest was 1890, bar goes to 1895→1900 → lowest stays 1890."""
        _highest, lowest = update_extremes(
            bar_high=190000,
            bar_low=189500,
        )
        assert lowest == 189000
