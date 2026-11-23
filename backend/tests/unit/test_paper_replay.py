from app.sandbox.model import Bar, Instruction, fees, match, sold_cost
from app.sandbox.parameters import PaperParameters


def test_market_prices_apply_adverse_slippage_within_the_actual_bar() -> None:
    cfg = PaperParameters(slippage_bps=100)
    bar = Bar(10000, 10020, 9900, 10000, 100)
    order = Instruction(symbol="NSE:AAA", side="buy", kind="market", quantity=2)
    assert match(order, bar, False, cfg).price == 10020
    assert match(order.model_copy(update={"side": "sell"}), bar, False, cfg).price == 9900


def test_limit_stop_and_gtt_respect_open_and_unknown_daily_event_order() -> None:
    cfg = PaperParameters(slippage_bps=0)
    bar = Bar(10000, 11000, 9500, 10000, 100)
    limit = Instruction(symbol="NSE:AAA", side="buy", kind="limit", quantity=2, limit_paisa=9600)
    assert match(limit, bar, False, cfg).price == 9600
    stop = Instruction(
        symbol="NSE:AAA", side="sell", kind="stop_market", quantity=2, trigger_paisa=9700
    )
    assert match(stop, bar, False, cfg).price == 9700
    assert match(stop, Bar(9600, 9700, 9000, 9500, 100), False, cfg).price == 9600
    gtt = Instruction(
        symbol="NSE:AAA",
        side="buy",
        kind="gtt",
        quantity=2,
        trigger_paisa=10200,
        limit_paisa=10000,
    )
    assert match(gtt, bar, False, cfg).arm
    assert match(gtt, bar, False, cfg).price is None
    assert match(gtt, bar, True, cfg).price == 10000


def test_simulated_fee_breakdown_and_cost_basis_preserve_paisa() -> None:
    cfg = PaperParameters()
    expected = {
        "brokerage": 300,
        "transaction_tax": 1000,
        "exchange": 30,
        "regulator": 1,
        "stamp": 150,
        "gst": 60,
    }
    assert fees(1_000_000, "buy", cfg) == expected
    assert fees(1_000_000, "sell", cfg) == {**expected, "stamp": 0}
    assert sold_cost(1001, 3, 2) == 667
    assert sold_cost(334, 1, 1) == 334
