"""Tests for derivatives ordering and margin."""

from app.sandbox.derivatives import (
    ContractSpec,
    contract_key,
    margin_required,
    sufficient_margin,
)


class TestContractKey:
    """Generate unique contract identifiers."""

    def test_equity_key(self) -> None:
        """Equity contract key is just the symbol."""
        key = contract_key("NSE:RELIANCE", None, None)
        assert key == "NSE:RELIANCE"

    def test_futures_key(self) -> None:
        """Futures contract key includes symbol."""
        key = contract_key("NSE:NIFTY1DEC26", "2026-12-31", None)
        assert key == "NSE:NIFTY1DEC26"

    def test_option_ce_key(self) -> None:
        """Option CE contract key."""
        key = contract_key("NSE:NIFTY1DEC26C26000", "2026-12-31", 2600000)
        assert key == "NSE:NIFTY1DEC26C26000"


class TestMarginRequired:
    """Calculate margin for different instrument types."""

    def test_equity_margin_20_percent(self) -> None:
        """Equity 100 @ ₹2000 with 20% margin → ₹40,000 required."""
        spec = ContractSpec(
            symbol="NSE:RELIANCE",
            contract_type="equity",
            multiplier=1,
            margin_initial=0,
        )
        margin = margin_required(
            contract_type="equity",
            quantity=100,
            price_paisa=200000,  # ₹2000
            margin_spec=spec,
        )
        # Notional: 100 * 200000 = 20000000 (₹2,00,000)
        # Margin 20%: 4000000 paisa (₹40,000)
        assert margin == 4000000

    def test_futures_margin_per_contract(self) -> None:
        """Futures 1 lot with margin requirement → margin applied."""
        spec = ContractSpec(
            symbol="NSE:NIFTY1DEC26",
            contract_type="fut",
            expiry_date="2026-12-31",
            multiplier=50,  # 1 Nifty lot = 50 contracts
            margin_initial=500000,  # ₹5000 per lot
        )
        margin = margin_required(
            contract_type="fut",
            quantity=1,  # 1 lot
            price_paisa=800000,  # Price doesn't matter for futures margin
            margin_spec=spec,
        )
        # 500000 * 1 = 500000 paisa
        assert margin == 500000

    def test_option_margin(self) -> None:
        """Option CE 100 contracts with margin spec."""
        spec = ContractSpec(
            symbol="NSE:NIFTY1DEC26C26000",
            contract_type="opt_ce",
            expiry_date="2026-12-31",
            strike_paisa=2600000,
            margin_initial=100000,  # ₹1000 per contract
        )
        margin = margin_required(
            contract_type="opt_ce",
            quantity=100,
            price_paisa=50000,  # Premium ₹500
            margin_spec=spec,
        )
        # 100000 * 100 = 10000000 paisa (₹100,000)
        assert margin == 10000000


class TestSufficientMargin:
    """Check if margin is available for new orders."""

    def test_sufficient_margin_no_buffer(self) -> None:
        """Available 50000, required 40000 → sufficient."""
        is_sufficient = sufficient_margin(
            available_margin=5000000,  # ₹50,000
            required_margin=4000000,  # ₹40,000
            buffer_paisa=0,
        )
        assert is_sufficient is True

    def test_exact_margin_no_buffer(self) -> None:
        """Available == required → sufficient."""
        is_sufficient = sufficient_margin(
            available_margin=4000000,
            required_margin=4000000,
            buffer_paisa=0,
        )
        assert is_sufficient is True

    def test_insufficient_margin(self) -> None:
        """Available 30000, required 40000 → insufficient."""
        is_sufficient = sufficient_margin(
            available_margin=3000000,
            required_margin=4000000,
            buffer_paisa=0,
        )
        assert is_sufficient is False

    def test_buffer_reduces_available(self) -> None:
        """Available 50000, required 40000, buffer 15000 → insufficient."""
        is_sufficient = sufficient_margin(
            available_margin=5000000,  # ₹50,000
            required_margin=4000000,  # ₹40,000
            buffer_paisa=1500000,  # ₹15,000 buffer
        )
        # Need: 40000 + 15000 = 55000, have 50000
        assert is_sufficient is False

    def test_buffer_with_enough_margin(self) -> None:
        """Available 60000, required 40000, buffer 15000 → sufficient."""
        is_sufficient = sufficient_margin(
            available_margin=6000000,
            required_margin=4000000,
            buffer_paisa=1500000,
        )
        # Need: 40000 + 15000 = 55000, have 60000
        assert is_sufficient is True
