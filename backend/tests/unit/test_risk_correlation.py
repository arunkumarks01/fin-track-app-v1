from app.risk.correlation import correlation_bps


def test_correlation_uses_returns_and_requires_real_variation() -> None:
    a = [100, 110, 99, 118, 110]
    assert correlation_bps(a, [v * 2 for v in a], 4) == 10000
    inverse = correlation_bps(a, [100, 90, 99, 80, 87], 4)
    assert inverse is not None
    assert inverse < -9500
    assert correlation_bps(a, [100] * 5, 4) is None
    assert correlation_bps(a[:3], a[:3], 4) is None
    assert correlation_bps([100, 0], [100, 100], 1) is None
