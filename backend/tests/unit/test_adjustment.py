"""Golden tests for measure-don't-model (Phase 1c).

Each case builds an as-traded NSE series, derives "Kite" bars by applying known cumulative
factors to every day BEFORE each ex-date (rounded to paisa, as Kite does), and checks that the
analysis recovers the windows, factors and classifications — never more, never less.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from app.data.adjustment import (
    Action,
    Analysis,
    DayObs,
    Minute,
    Nse,
    Tol,
    analyse,
    grain_ratio,
    index_days,
)

TOL = Tol(
    paisa=2.5,
    grain_agree=0.002,
    flat=0.0005,
    match=0.005,
    unexplained_small=0.03,
    min_window_days=5,
)
START = date(2024, 1, 1)


@dataclass(frozen=True)
class Step:
    ex_date: date
    price: float  # Kite multiplies prices of days before ex_date by this…
    volume: float = 1.0  # …and volumes by this (share-count events only)
    grain: float = 1.0  # extra factor on the 1m grain only, for days before ex_date
    subtract: float = 0.0  # …or SUBTRACTS this (paisa, as-traded terms) — Kite's dividends


def kite_map(d: date, steps: Sequence[Step], p: float) -> float:
    """Kite's adjusted price: each later action is applied on top of the earlier ones."""
    for s in sorted(steps, key=lambda s: s.ex_date):
        if d < s.ex_date:
            p = p * s.price - s.subtract
    return p


def tick(x: float, step: int) -> int:
    return round(x / step) * step


def weekdays(n: int, start: date = START) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def build(
    n: int,
    steps: Sequence[Step] = (),
    *,
    seed: int = 7,
    nse_missing: Sequence[int] = (),
    kite_close_bump: dict[int, int] | None = None,
    minute: bool = True,
    tick_paisa: int = 1,
    jitter: int = 0,
) -> list[DayObs]:
    rnd = random.Random(seed)  # noqa: S311 — deterministic test data, not crypto
    days = weekdays(n)
    price = 50_000  # ₹500.00 in paisa
    out = []
    for i, d in enumerate(days):
        price = max(1_000, price + rnd.randint(-900, 900))
        o, c = price + rnd.randint(-300, 300), price
        h, lo = max(o, c) + rnd.randint(0, 400), min(o, c) - rnd.randint(0, 400)
        vol = rnd.randint(100_000, 900_000)
        v = g = 1.0
        for s in steps:
            if d < s.ex_date:
                v, g = v * s.volume, g * s.grain
        k = [
            tick(
                kite_map(d, steps, x) + (rnd.randint(-jitter, jitter) if jitter else 0), tick_paisa
            )
            for x in (o, h, lo, c)
        ]
        k[1], k[2] = max(k), min(k)
        if kite_close_bump and i in kite_close_bump:
            k[3] += kite_close_bump[i]
            k[1] = max(k[1], k[3])
        nse = None if i in nse_missing else Nse(o, h, lo, c, vol, price)
        # the 1m series: same grain factor; its low misses the official extreme (normal)
        m = (
            Minute(round(k[0] * g), round(k[1] * g), round(k[2] * g * 1.004), round(vol * v * 0.98))
            if minute
            else None
        )
        out.append(DayObs(d, k[0], k[1], k[2], k[3], round(vol * v), nse, m))
    return out


def act(
    ex: date,
    purpose: str,
    *,
    kinds: Sequence[str],
    split_from: int | None = None,
    split_to: int | None = None,
    bonus_new: int | None = None,
    bonus_held: int | None = None,
    dividend_paisa: int | None = None,
) -> Action:
    return Action(
        (abs(hash((ex, purpose))) % 10_000,),
        ex,
        tuple(kinds),
        purpose,
        split_from,
        split_to,
        bonus_new,
        bonus_held,
        dividend_paisa,
    )


def quality(a: Analysis, d: date) -> str:
    return next(r.quality for r in a.days if r.d == d)


DAYS = weekdays(120)
EX = DAYS[60]


def test_split_is_measured_and_explained() -> None:
    days = build(120, [Step(EX, 0.1, volume=10.0)])
    a = analyse(
        days,
        [act(EX, "FVSPLT FRM RS 10 TO RE 1", kinds=["split"], split_from=1000, split_to=100)],
        TOL,
    )
    assert [(e.effective_date, e.classification) for e in a.events] == [(EX, "explained")]
    assert a.events[0].ratio == pytest.approx(0.1, rel=1e-4)
    before = [r for r in a.days if r.d < EX]
    after = [r for r in a.days if r.d >= EX]
    assert {r.quality for r in a.days} == {"measured"}
    assert {round(r.f_daily, 6) for r in after} == {1.0}  # type: ignore[arg-type]
    # a and b trade off within the rounding; the MAP is what's identified: at the window's
    # typical price it is x0.1, and every accepted day fits it within ±2.5 paisa
    b0 = before[0]
    assert b0.f_daily is not None
    assert b0.b_daily is not None
    typical = statistics.median(o.nse.close for o in days if o.d < EX and o.nse)
    assert (b0.f_daily * typical + b0.b_daily) / typical == pytest.approx(0.1, rel=1e-4)
    assert_measured_days_fit(days, a)
    # volume has its own measured factor: x10 before the split, x1 after
    assert before[0].v_daily == pytest.approx(10.0, rel=1e-6)
    assert after[0].v_daily == pytest.approx(1.0, rel=1e-6)


def test_bonus_one_for_three() -> None:
    days = build(120, [Step(EX, 0.75, volume=4 / 3)])
    a = analyse(days, [act(EX, "BONUS 1:3", kinds=["bonus"], bonus_new=1, bonus_held=3)], TOL)
    assert [e.classification for e in a.events] == ["explained"]
    assert a.events[0].ratio == pytest.approx(0.75, rel=1e-4)


def test_dividend_with_stated_amount_is_explained_against_official_prev_close() -> None:
    days = build(120)
    prev = days[59].nse.close  # type: ignore[union-attr]
    div = 1_000  # ₹10
    days = build(120, [Step(EX, (prev - div) / prev)])
    a = analyse(days, [act(EX, "DIV - RS 10 PER SH", kinds=["dividend"], dividend_paisa=div)], TOL)
    assert [e.classification for e in a.events] == ["explained"]


def test_dividend_without_amount_is_attributed_not_explained() -> None:
    days = build(120, [Step(EX, 0.985)])
    a = analyse(days, [act(EX, "INTERIM DIVIDEND", kinds=["dividend"])], TOL)
    assert [e.classification for e in a.events] == ["attributed"]
    assert {r.quality for r in a.days} == {"measured"}


def test_kite_ignoring_a_dividend_is_no_event() -> None:
    days = build(120)  # Kite did not adjust
    a = analyse(days, [act(EX, "DIV - RS 3 PER SH", kinds=["dividend"], dividend_paisa=300)], TOL)
    assert a.events == []
    assert {r.quality for r in a.days} == {"measured"}


def test_small_unexplained_step_is_badged_and_kept() -> None:
    a = analyse(build(120, [Step(EX, 0.99)]), [], TOL)
    assert [e.classification for e in a.events] == ["unexplained_small"]
    assert {r.quality for r in a.days} == {"measured"}  # kept, badged via the event


def test_large_unexplained_step_makes_both_windows_suspect() -> None:
    a = analyse(build(120, [Step(EX, 0.8)]), [], TOL)
    assert [e.classification for e in a.events] == ["suspect"]
    assert {r.quality for r in a.days} == {"suspect"}  # never derive as-traded from it


def test_action_whose_ratio_disagrees_is_not_explained() -> None:
    # NSE says 1:10 split, Kite stepped x0.5: big disagreement → suspect, not "explained"
    days = build(120, [Step(EX, 0.5)])
    a = analyse(
        days,
        [act(EX, "FV SPLT FRM RS 10 TO RE 1", kinds=["split"], split_from=1000, split_to=100)],
        TOL,
    )
    assert [e.classification for e in a.events] == ["suspect"]


def test_rounding_micro_noise_never_makes_a_jump() -> None:
    # a non-round factor: every Kite price is rounded, residuals up to ±0.5 paisa each side
    for seed in range(20):
        a = analyse(build(250, [Step(weekdays(250)[200], 0.912345)], seed=seed), [], TOL)
        assert len(a.events) == 1, seed  # exactly the one real step — no rounding jumps
        assert {r.quality for r in a.days} <= {"measured", "suspect"}, seed


def test_single_deviating_day_is_a_blip_not_two_jumps() -> None:
    days = build(120, kite_close_bump={30: 250})  # ₹2.50 off on one day only
    a = analyse(days, [], TOL)
    assert a.events == []
    assert a.blips == [DAYS[30]]
    assert quality(a, DAYS[29]) == quality(a, DAYS[31]) == "measured"


def test_unmeasurable_days_are_carried_inside_a_flat_window_only() -> None:
    days = build(120, nse_missing=[10, 119])
    a = analyse(days, [], TOL)
    assert quality(a, DAYS[10]) == "carried"  # NSE row missing, window flat around it
    assert quality(a, DAYS[119]) == "unmeasurable"  # newest day, nothing after it to confirm
    assert a.events == []


def test_no_nse_data_at_all_is_unmeasurable_not_a_crash() -> None:
    a = analyse(build(30, nse_missing=range(30)), [], TOL)
    assert {r.quality for r in a.days} == {"unmeasurable"}
    assert a.events == []


def test_minute_grain_step_at_rights_date_is_measured_separately() -> None:
    # RELIANCE pattern: 1d not adjusted for the rights issue, 1m adjusted x0.991
    days = build(120, [Step(EX, 1.0, grain=0.991)])
    a = analyse(days, [act(EX, "RIGHTS 1:15 @ PREMIUM RS 1247", kinds=["other"])], TOL)
    assert [e.classification for e in a.events] == ["attributed"]
    assert a.events[0].g_ratio == pytest.approx(0.991, rel=5e-4)
    g_before = {round(r.g_minute, 3) for r in a.days if r.d < EX}  # type: ignore[arg-type]
    g_after = {round(r.g_minute, 3) for r in a.days if r.d >= EX}  # type: ignore[arg-type]
    assert (g_before, g_after) == ({0.991}, {1.0})


def test_grain_ratio_needs_two_of_three_to_agree() -> None:
    d = DayObs(DAYS[0], 10_000, 10_500, 9_800, 10_100, 1, None, Minute(9_910, 10_405, 9_900, 1))
    assert grain_ratio(d, TOL) == pytest.approx(0.991, rel=1e-3)  # open & high agree
    bad = DayObs(DAYS[0], 10_000, 10_500, 9_800, 10_100, 1, None, Minute(9_500, 10_400, 9_900, 1))
    assert grain_ratio(bad, TOL) is None  # no two agree → unmeasured that day, never guessed


def test_newest_window_off_one_is_an_official_mismatch() -> None:
    # the last 6 days are all on a different basis than NSE's official prices
    days = build(120, [Step(DAYS[115], 1.0)])
    days = [
        DayObs(
            o.d,
            round(o.open * 0.995),
            round(o.high * 0.995),
            round(o.low * 0.995),
            round(o.close * 0.995),
            o.volume,
            o.nse,
            o.minute,
        )
        if o.d >= DAYS[114]
        else o
        for o in days
    ]
    a = analyse(days, [], TOL)
    assert "official_mismatch" in {e.classification for e in a.events}


def test_provisional_newest_bar_is_flagged_as_blip() -> None:
    # 2026-11-19 pattern: only the newest day's close differs from the official close
    a = analyse(build(120, kite_close_bump={119: 130}), [], TOL)
    assert a.blips == [DAYS[119]]
    assert a.events == []


def test_indices_are_not_applicable_with_unit_factors() -> None:
    rows = index_days(DAYS[:3])
    assert {(r.quality, r.f_daily, r.g_minute, r.v_daily) for r in rows} == {
        ("not_applicable", 1.0, 1.0, 1.0)
    }


def assert_measured_days_fit(days: Sequence[DayObs], a: Analysis) -> None:
    """Property: no accepted day exceeds the tolerance on ANY of its four prices."""
    by = {o.d: o for o in days}
    for r in a.days:
        if r.quality == "measured":
            o = by[r.d]
            assert o.nse is not None
            assert r.f_daily is not None
            assert r.b_daily is not None
            bound = r.tol_paisa if r.tol_paisa is not None else TOL.paisa
            assert bound in (TOL.paisa, 5.0)  # the approved floor, or half a 10-paisa grid
            for n, k in (
                (o.nse.open, o.open),
                (o.nse.high, o.high),
                (o.nse.low, o.low),
                (o.nse.close, o.close),
            ):
                assert abs(k - (r.f_daily * n + r.b_daily)) <= bound + 1e-6, (r.d, n, k)


def test_subtractive_dividend_is_affine_and_explained() -> None:
    # ASHOKLEY pattern: Kite subtracts the dividend (₹2.45) from earlier prices
    days = build(120, [Step(EX, 1.0, subtract=245)], tick_paisa=5)
    a = analyse(
        days, [act(EX, "DIV - RS 2.45 PER SH", kinds=["dividend"], dividend_paisa=245)], TOL
    )
    assert [e.classification for e in a.events] == ["explained"]
    before = next(r for r in a.days if r.d == DAYS[10])
    assert before.f_daily == pytest.approx(1.0, abs=2e-4)
    assert before.b_daily == pytest.approx(-245, abs=3)
    assert_measured_days_fit(days, a)


def test_bonus_plus_subtracted_dividends_fit_exactly_and_invert_to_nse() -> None:
    # a = 0.5 (1:1 bonus), b = -₹2.45 (dividends subtracted), Kite rounded to the 5-paisa tick
    steps = [Step(DAYS[30], 1.0, subtract=245), Step(DAYS[90], 0.5, volume=2.0)]
    days = build(120, steps, tick_paisa=5)
    acts = [
        act(DAYS[30], "DIV - RS 2.45 PER SH", kinds=["dividend"], dividend_paisa=245),
        act(DAYS[90], "BONUS 1:1", kinds=["bonus"], bonus_new=1, bonus_held=1),
    ]
    a = analyse(days, acts, TOL)
    assert [e.classification for e in a.events] == ["explained", "explained"]
    assert {r.quality for r in a.days} == {"measured"}
    assert_measured_days_fit(days, a)
    # golden inverse: (Kite - b) / a recovers NSE's official price within the tick rounding
    by = {r.d: r for r in a.days}
    for o in days:
        r = by[o.d]
        assert o.nse is not None
        assert r.f_daily is not None
        assert r.b_daily is not None
        for n, k in (
            (o.nse.open, o.open),
            (o.nse.high, o.high),
            (o.nse.low, o.low),
            (o.nse.close, o.close),
        ):
            assert abs((k - r.b_daily) / r.f_daily - n) <= (TOL.paisa + 1e-6) / r.f_daily


def test_short_window_between_two_actions_is_underdetermined() -> None:
    e1, e2 = DAYS[60], DAYS[63]  # only 3 sessions between two real steps
    days = build(120, [Step(e1, 0.98), Step(e2, 0.5, volume=2.0)])
    acts = [
        act(e1, "DIV - RS 10 PER SH", kinds=["dividend"], dividend_paisa=1000),
        act(e2, "BONUS 1:1", kinds=["bonus"], bonus_new=1, bonus_held=1),
    ]
    a = analyse(days, acts, TOL)
    assert {quality(a, d) for d in DAYS[60:63]} == {"underdetermined"}
    assert quality(a, DAYS[59]) == quality(a, DAYS[63]) == "measured"


def test_noise_beyond_tolerance_is_never_accepted() -> None:
    # Kite jittered ±6 paisa: no window may claim a fit within ±2.5 for those days
    days = build(120, jitter=6, seed=3)
    a = analyse(days, [], TOL)
    assert_measured_days_fit(days, a)
    flagged = sum(1 for r in a.days if r.quality in ("blip", "underdetermined", "suspect"))
    assert flagged > 0


def test_tick_rounding_never_makes_steps() -> None:
    for seed in range(10):
        days = build(250, tick_paisa=5, seed=seed)
        a = analyse(days, [], TOL)
        assert a.events == [], seed
        assert {r.quality for r in a.days} == {"measured"}, seed


def test_zero_kite_volume_never_yields_a_zero_volume_factor() -> None:
    # real: Kite reports volume 0 on some days (ADANIENT 2018-10) — such days can't measure V
    days = build(30)
    days = [
        DayObs(o.d, o.open, o.high, o.low, o.close, 0, o.nse, o.minute) if i < 8 else o
        for i, o in enumerate(days)
    ]
    a = analyse(days, [], TOL)
    assert all(r.v_daily is None or r.v_daily > 0 for r in a.days)
    assert all(r.v_minute is None or r.v_minute > 0 for r in a.days)
    only_zero = [DayObs(o.d, o.open, o.high, o.low, o.close, 0, o.nse, o.minute) for o in build(10)]
    assert {r.v_daily for r in analyse(only_zero, [], TOL).days} == {None}


# --- grid-aware tolerance (approved 2026-11-20): half the window's measured Kite grid, ≥ ±2.5 --


def test_ten_paisa_grid_windows_fit_within_half_a_ten_paisa_step() -> None:
    # ADANIENT 2015 pattern: every Kite price a multiple of 10 paisa → rounding is up to ±5
    for seed in range(10):
        days = build(250, [Step(weekdays(250)[200], 0.28766)], tick_paisa=10, seed=seed)
        a = analyse(days, [act(weekdays(250)[200], "DEMERGER", kinds=["other"])], TOL)
        assert {r.quality for r in a.days} == {"measured"}, seed
        assert {r.tol_paisa for r in a.days} == {5.0}, seed
        assert [e.classification for e in a.events] == ["attributed"], seed
        assert_measured_days_fit(days, a)


def test_ten_paisa_grid_bonus_and_subtraction_invert_within_the_grid_bound() -> None:
    steps = [Step(DAYS[30], 1.0, subtract=250), Step(DAYS[90], 0.5, volume=2.0)]
    days = build(120, steps, tick_paisa=10)
    acts = [
        act(DAYS[30], "DIV - RS 2.50 PER SH", kinds=["dividend"], dividend_paisa=250),
        act(DAYS[90], "BONUS 1:1", kinds=["bonus"], bonus_new=1, bonus_held=1),
    ]
    a = analyse(days, acts, TOL)
    assert [e.classification for e in a.events] == ["explained", "explained"]
    by = {r.d: r for r in a.days}
    for o in days:
        r = by[o.d]
        assert o.nse is not None
        assert r.f_daily is not None
        assert r.b_daily is not None
        assert r.tol_paisa == 5.0
        for n, k in ((o.nse.open, o.open), (o.nse.close, o.close)):
            assert abs((k - r.b_daily) / r.f_daily - n) <= (5.0 + 1e-6) / r.f_daily


def test_five_paisa_grid_keeps_the_strict_bound() -> None:
    # jitter of ±4 paisa on a 5-paisa grid must NOT be accepted (bound stays ±2.5)
    days = build(120, jitter=4, tick_paisa=5, seed=11)
    a = analyse(days, [], TOL)
    assert {r.tol_paisa for r in a.days if r.tol_paisa is not None} == {TOL.paisa}
    assert_measured_days_fit(days, a)
    assert any(r.quality in ("blip", "underdetermined") for r in a.days)


def test_too_few_prices_to_prove_a_ten_paisa_grid_keeps_the_strict_bound() -> None:
    # 3 sessions between two actions, all on multiples of 10 by chance: not evidence enough
    e1, e2 = DAYS[60], DAYS[63]
    days = build(120, [Step(e1, 0.98), Step(e2, 0.5, volume=2.0)], tick_paisa=10)
    acts = [
        act(e1, "DIV - RS 10 PER SH", kinds=["dividend"], dividend_paisa=1000),
        act(e2, "BONUS 1:1", kinds=["bonus"], bonus_new=1, bonus_held=1),
    ]
    a = analyse(days, acts, TOL)
    assert {r.tol_paisa for r in a.days if DAYS[60] <= r.d < DAYS[63]} == {TOL.paisa}


# --- matching on the affine line, and on the actions Kite actually applied (round 4) ------


def test_bonus_under_an_existing_offset_is_matched_on_the_line_not_a_blended_ratio() -> None:
    # IOC pattern: a later subtracted dividend leaves b = -3000 on both sides of an earlier
    # bonus; the slope halves exactly, b is unchanged — a blended ratio would say x0.468
    bonus, div = DAYS[40], DAYS[90]
    days = build(120, [Step(bonus, 0.5, volume=2.0), Step(div, 1.0, subtract=3000)])
    acts = [
        act(bonus, "BONUS 1:1", kinds=["bonus"], bonus_new=1, bonus_held=1),
        act(div, "DIV - RS 30 PER SH", kinds=["dividend"], dividend_paisa=3000),
    ]
    a = analyse(days, acts, TOL)
    assert [(e.effective_date, e.classification) for e in a.events] == [
        (bonus, "explained"),
        (div, "explained"),
    ]


def test_kite_applying_the_bonus_but_not_the_same_day_dividend_is_explained() -> None:
    # INFY 2018-09-11 pattern: BONUS 1:1 and a ₹30.50 dividend share the ex-date; Kite x0.5
    days = build(120, [Step(EX, 0.5, volume=2.0)])
    acts = [
        act(EX, "BONUS 1:1", kinds=["bonus"], bonus_new=1, bonus_held=1),
        act(EX, "AGM/DIV-RS30.50 PER SHARE", kinds=["dividend"], dividend_paisa=3050),
    ]
    a = analyse(days, acts, TOL)
    assert [e.classification for e in a.events] == ["explained"]
    assert "BONUS 1:1" in a.events[0].detail


def test_only_the_listing_that_matches_explains_the_step() -> None:
    # CUB 2017 pattern: two bonus listings on one date; Kite applied 1:10 (x10/11)
    days = build(120, [Step(EX, 10 / 11, volume=1.1)])
    acts = [
        act(EX, "BONUS 1:1", kinds=["bonus"], bonus_new=1, bonus_held=1),
        act(EX, "BONUS 1:10", kinds=["bonus"], bonus_new=1, bonus_held=10),
    ]
    assert [e.classification for e in analyse(days, acts, TOL).events] == ["explained"]


def test_none_applied_is_never_an_explanation() -> None:
    # a 2% step on a dividend day the dividend can't explain (₹1 on ₹500 = 0.2%)
    days = build(120, [Step(EX, 0.98)])
    acts = [act(EX, "DIV - RS 1 PER SH", kinds=["dividend"], dividend_paisa=100)]
    assert [e.classification for e in analyse(days, acts, TOL).events] == ["unexplained_small"]
