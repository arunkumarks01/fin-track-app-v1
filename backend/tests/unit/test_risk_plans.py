from app.risk.parameters import RiskParameters
from app.risk.plans import PriceContext, build_levels, trail_stop
from app.risk.sizing import Kelly, Portfolio, checks, size


def test_structure_and_atr_levels_with_risk_at_the_worst_entry() -> None:
    levels = build_levels(
        PriceContext(10_000, 10_200, 9_800, 200, 5, (9_900, 10_700)),
        "long",
        RiskParameters(),
    )
    assert levels is not None
    assert (levels.trigger_paisa, levels.entry_limit_paisa, levels.stop_paisa) == (
        10000,
        10050,
        9850,
    )
    assert levels.target_basis == "structure"
    assert (levels.target_paisa, levels.risk_per_unit_paisa, levels.reward_risk_bps) == (
        10700,
        200,
        32500,
    )
    assert (levels.trailing_activation_paisa, levels.trailing_distance_paisa) == (10250, 300)
    assert trail_stop("long", 9850, 10200, 10250, 300, 5) == 9850
    assert trail_stop("long", 9850, 10257, 10250, 300, 5) == 9955
    assert trail_stop("long", 9955, 10250, 10250, 300, 5) == 9955


def test_short_levels_round_in_the_conservative_direction() -> None:
    levels = build_levels(
        PriceContext(10_001, 10_200, 9_800, 200, 5, (10_100, 9500)),
        "short",
        RiskParameters(),
    )
    assert levels is not None
    assert (levels.trigger_paisa, levels.entry_limit_paisa, levels.stop_paisa) == (
        10000,
        9955,
        10150,
    )
    assert (levels.target_paisa, levels.risk_per_unit_paisa) == (9500, 195)
    assert trail_stop("short", 10150, 9601, 9700, 300, 5) == 9905
    assert trail_stop("short", 9905, 9650, 9700, 300, 5) == 9905


def test_invalid_or_zero_risk_inputs_are_unavailable() -> None:
    cfg = RiskParameters()
    assert build_levels(PriceContext(20, 30, 25, 5, 5, ()), "long", cfg) is None
    assert build_levels(PriceContext(20, 30, 10, 0, 5, ()), "long", cfg) is None


def test_sizes_use_both_budgets_and_round_down_to_whole_lots() -> None:
    cfg = RiskParameters(paper_capital_paisa=1_000_000, fixed_lots=20, correlation_limit_bps=7000)
    levels = build_levels(PriceContext(10000, 10200, 9800, 200, 5, (9900, 10700)), "long", cfg)
    assert levels is not None
    portion = size(levels, 5, "future", cfg, "fixed_risk")
    assert (portion.risk_budget_paisa, portion.notional_cap_paisa) == (10_000, 100_000)
    assert (portion.lots, portion.units, portion.risk_paisa, portion.notional_paisa) == (
        1,
        5,
        1_000,
        50_250,
    )
    correlated = Portfolio(1_000_000, 0, 1, 0, correlation_bps=8000)
    assert size(levels, 5, "future", cfg, "fixed_risk", correlated).lots == 0
    assert size(levels, 5, "future", cfg, "fixed_lots", correlated).lots == 20


def test_realistic_mode_enforces_caps_and_learning_only_displays_them() -> None:
    cfg = RiskParameters(
        paper_capital_paisa=1_000_000, max_heat_bps=200, max_sector_notional_bps=1000
    )
    levels = build_levels(PriceContext(10000, 10200, 9800, 200, 5, (9900, 10700)), "long", cfg)
    assert levels is not None
    sizing = size(levels, 5, "underlying", cfg, "fixed_risk")
    holdings = Portfolio(200_000, -30_000, 5, 5, 19_500, 60_000)
    learning = checks(levels.reward_risk_bps, sizing, holdings, cfg, "learning", "underlying")
    realistic = checks(levels.reward_risk_bps, sizing, holdings, cfg, "realistic", "underlying")
    assert learning["blocks"] is False
    assert realistic["blocks"] is True
    assert learning["warnings"] == realistic["warnings"]
    assert set(realistic["warnings"]) == {
        "Daily loss circuit breaker reached",
        "Daily trade count reached",
        "Maximum open positions reached",
        "Cash buffer would be breached",
        "Portfolio heat cap exceeded",
        "Sector exposure cap exceeded",
    }
    assert (
        checks(levels.reward_risk_bps, sizing, None, cfg, "realistic", "underlying")["blocks"]
        is True
    )


def test_kelly_stays_locked_until_validated_data_exist() -> None:
    cfg = RiskParameters(paper_capital_paisa=1_000_000)
    levels = build_levels(PriceContext(10000, 10200, 9800, 200, 5, (9900, 10700)), "long", cfg)
    assert levels is not None
    first = size(levels, 1, "underlying", cfg, "kelly", kelly=Kelly(60, 100, 1000, 500, 179))
    assert first.units is None
    ready = size(levels, 1, "underlying", cfg, "kelly", kelly=Kelly(60, 100, 1000, 500, 180))
    assert ready.units == 9
