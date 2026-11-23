import pytest

from app.backtest.strategies import DAILY_LONG_BASELINES, daily_long_spec


def test_first_backtest_cohort_is_versioned_daily_long_only() -> None:
    assert [spec.strategy_id for spec in DAILY_LONG_BASELINES] == [
        "year_breakout_long",
        "ema_golden_cross_long",
        "rsi_bounce_long",
        "bollinger_squeeze_long",
        "relative_strength_long",
    ]
    assert all(spec.entry == "next_session_market" for spec in DAILY_LONG_BASELINES)
    assert all(
        spec.stop_loss_bps > 0 and spec.target_bps > spec.stop_loss_bps
        for spec in DAILY_LONG_BASELINES
    )
    assert all(spec.one_position_per_symbol for spec in DAILY_LONG_BASELINES)


def test_unknown_baseline_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown daily-long"):
        daily_long_spec("invented_v1")
