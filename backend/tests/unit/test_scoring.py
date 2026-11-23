from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.cache import MemoryCache
from app.scoring.calibration import conviction
from app.scoring.contracts import option_candidate
from app.scoring.news_research import NewsResearch
from app.scoring.parameters import ScoringParameters
from app.scoring.rules import BUILT_INS, assess
from tests.fixtures.clock import FrozenClock

NOW = datetime(2026, 11, 21, tzinfo=UTC)


def test_52_week_breakout_uses_the_reported_boundary_and_marks_missing_evidence() -> None:
    rule = next(s for s in BUILT_INS if s.id == "year_breakout_long")
    below = assess(rule, {"close_paisa": 15000, "prior_252_high_paisa": 15000}, {}, False)
    assert below["status"] == "no_match"
    above = assess(rule, {"close_paisa": 15001, "prior_252_high_paisa": 15000}, {}, False)
    assert above["status"] == "matched"
    assert above["evidence"][0]["observed"] == 15001
    assert above["evidence"][0]["threshold"] == 15000
    assert assess(rule, {"close_paisa": 15001}, {}, False)["status"] == "unavailable"


def test_opening_range_needs_two_candles_and_shorts_default_to_disabled() -> None:
    long = next(s for s in BUILT_INS if s.id == "opening_range_long")
    high = {"close_paisa": 110, "opening_range_high_paisa": 100}
    before = {"close_paisa": 95, "opening_range_high_paisa": 100}
    assert assess(long, high, {}, False)["status"] == "unavailable"
    assert assess(long, high, high, False)["status"] == "no_match"
    assert assess(long, high, before, False)["status"] == "matched"
    short = next(s for s in BUILT_INS if s.id == "opening_range_short")
    below = {"close_paisa": 80, "opening_range_low_paisa": 90}
    above = {"close_paisa": 95, "opening_range_low_paisa": 90}
    assert assess(short, below, above, False)["status"] == "disabled"
    assert assess(short, below, above, True)["status"] == "matched"


def test_contract_research_requires_fresh_quotes_and_checks_spread_and_iv() -> None:
    chain = {
        "status": "ok",
        "stale": False,
        "spot_paisa": 200_000,
        "snapshot_at": NOW.isoformat(),
        "expiries": [
            {
                "expiry": "2026-11-26",
                "days_to_expiry": 5,
                "contracts": [
                    {
                        "type": "CE",
                        "strike_paisa": 200_000,
                        "midpoint_twice_paisa": 10_000,
                        "bid_paisa": 4_950,
                        "ask_paisa": 5_050,
                        "oi": 2_000,
                        "iv_pct": 20,
                    }
                ],
            }
        ],
    }
    context = {"close_paisa": 200_000, "ema_50_paisa": 199_000}
    opts = ScoringParameters()
    result = option_candidate(chain, "swing", "CE", context, opts)
    assert result["status"] == "matched"
    assert result["contract"]["strike_paisa"] == 200_000
    assert (
        option_candidate({**chain, "stale": True}, "swing", "CE", context, opts)["status"]
        == "unavailable"
    )
    assert option_candidate(chain, "swing", "PE", context, opts)["status"] == "unavailable"


def test_conviction_remains_unavailable_without_evidence() -> None:
    assert conviction(None, 30)["win_probability_pct"] is None
    result = {"observations": 40, "wins": 22, "evaluated_through": NOW, "available_at": NOW}
    assert conviction(result, 50)["status"] == "uncalibrated"
    assert conviction(result, 30)["win_probability_pct"] == pytest.approx(55)


@pytest.mark.parametrize(
    "reply",
    [
        "broken JSON",
        '{"sentiment":"neutral","material_event":false,"sources":[999]}',
        '{"sentiment":"neutral","material_event":false,"sources":[]}',
    ],
)
async def test_news_provider_fails_closed_and_cools_down(reply: str) -> None:
    calls = 0

    async def complete(prompt: str, seconds: float) -> str:
        nonlocal calls
        calls += 1
        return reply

    news = NewsResearch(MemoryCache(FrozenClock(NOW), 10), ScoringParameters(), complete)
    sources = [{"id": "7", "link": "https://example.com/7", "title": "Headline", "summary": ""}]
    assert (await news.review("NSE:TEST", sources))["status"] == "unknown"
    assert (await news.review("NSE:TEST", sources))["status"] == "unknown"
    assert calls == 1


async def test_news_provider_success_is_grounded_and_cached() -> None:
    calls = 0

    async def complete(prompt: str, seconds: float) -> str:
        nonlocal calls
        calls += 1
        return '{"sentiment":"positive","material_event":true,"sources":[7,7]}'

    news = NewsResearch(MemoryCache(FrozenClock(NOW), 10), ScoringParameters(), complete)
    sources = [{"id": "7", "link": "https://example.com/7", "title": "Headline", "summary": ""}]
    result = await news.review("NSE:TEST", sources)
    assert result["status"] == "classified"
    assert result["sources"] == [{"title": "Headline", "link": "https://example.com/7"}]
    assert news.status("NSE:TEST", sources) == result
    assert await news.review("NSE:TEST", sources) == result
    assert calls == 1
    assert news.status("NSE:OTHER", sources)["status"] == "unknown"
