"""Per-instrument session calendar (revised DEC-1B): each instrument against its OWN norm."""

from __future__ import annotations

from datetime import date, timedelta

from app.data.session_calendar import CalendarRules, classify, instrument_norms

RULES = CalendarRules(
    baseline_sessions=20, step_sessions=10, market_short_share=0.5, market_min_instruments=3
)


def sessions(counts: list[int], start: date = date(2026, 6, 1)) -> list[tuple[date, int]]:
    out, d = [], start
    for c in counts:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append((d, c))
        d += timedelta(days=1)
    return out


def test_own_norm_and_a_short_session() -> None:
    s = sessions([375] * 30 + [370] + [375] * 5)
    assert instrument_norms([c for _, c in s], RULES) == [375] * 36


def test_persistent_step_is_a_new_normal_from_its_first_session() -> None:
    # the closing auction: 375 -> 360 for good
    counts = [375] * 30 + [360] * 40
    assert instrument_norms(counts, RULES) == [375] * 30 + [360] * 40


def test_short_lived_change_is_not_a_new_normal() -> None:
    # a week of 374 (e.g. a feed gap) must stay "short", not become the norm
    counts = [375] * 30 + [374] * 9 + [375] * 20
    assert instrument_norms(counts, RULES) == [375] * 59


def test_too_few_sessions_is_unknown() -> None:
    assert instrument_norms([375] * 5, RULES) == [None] * 5


def test_market_wide_short_day_is_reported_not_flagged() -> None:
    # 2021-02-24 pattern: an outage — everyone low that day; one instrument short another day
    data = {}
    for i in range(6):
        counts = [375] * 30
        counts[25] = 224 - i * 10
        if i == 0:
            counts[10] = 370
        data[f"NSE:S{i}"] = sessions(counts)
    out = classify(data, {k: "NSE" for k in data}, RULES)
    s0 = {d: (exp, st) for d, _, exp, st in out["NSE:S0"]}
    outage = sessions([0] * 30)[25][0]
    tenth = sessions([0] * 30)[10][0]
    assert s0[outage] == (375, "market_short")
    assert s0[tenth] == (375, "short")  # an ordinary short session IS flagged
    assert all(st == "market_short" for k in data for d, _, _, st in out[k] if d == outage)


def test_indices_judged_against_their_own_norm_not_peers() -> None:
    # 4 indices at 375 while equities moved to 360: indices stay complete at 375
    data = {f"NSE:E{i}": sessions([375] * 20 + [360] * 20) for i in range(10)}
    data.update({f"NSE:I{i}": sessions([375] * 40) for i in range(4)})
    out = classify(data, {k: "NSE" for k in data}, RULES)
    assert {st for k in data for *_, st in out[k]} == {"complete"}


def test_phased_rollout_each_instrument_switches_on_its_own_date() -> None:
    early = sessions([375] * 20 + [360] * 40)
    late = sessions([375] * 35 + [360] * 25)  # e.g. MAHABANK joined later
    out = classify({"A": early, "B": late}, {"A": "NSE", "B": "NSE"}, RULES)
    assert {st for *_, st in out["A"]} == {"complete"}
    assert {st for *_, st in out["B"]} == {"complete"}


def test_excess_is_flagged() -> None:
    s = sessions([360] * 30 + [375] + [360] * 5)
    out = classify({"A": s}, {"A": "NSE"}, RULES)
    assert [st for *_, st in out["A"]].count("excess") == 1


def test_thin_market_never_declares_a_market_wide_day() -> None:
    # fewer instruments than the minimum: a short session stays a flagged short
    a = sessions([375] * 30)
    a[12] = (a[12][0], 300)
    b = sessions([375] * 30)
    b[12] = (b[12][0], 300)
    out = classify({"A": a, "B": b}, {"A": "BSE", "B": "BSE"}, RULES)
    assert out["A"][12][3] == "short"


def test_a_step_back_up_is_also_a_new_normal() -> None:
    """SEBI 15 Oct 2026 (from 2026-12-07): CTS for CAS stocks returns to 15:30, 360 → 375."""
    counts = [375] * 30 + [360] * 40 + [375] * 20
    assert instrument_norms(counts, RULES) == [375] * 30 + [360] * 40 + [375] * 20
