"""1e parsers: RSS/Atom (defused), FRED observations (exact scaled integers), calendar CSV."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from app.data.econ_calendar import CalendarImportError, parse_calendar_csv
from app.data.fred import FredError, parse_observations
from app.data.news import FeedError, parse_feed

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Markets</title><ttl>15</ttl>
<item><title>Sensex rises &amp; Nifty gains</title><link>https://example.com/a</link>
<description><![CDATA[<p>Stocks <b>rose</b> today.</p>]]></description>
<pubDate>Fri, 20 Nov 2026 15:45:00 +0530</pubDate></item>
<item><title>No link here</title></item>
<item><title>Bad date</title><link>https://example.com/b</link><pubDate>someday</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>RBI</title>
<entry><title>Press release</title><link rel="alternate" href="https://rbi.example/p1"/>
<updated>2026-11-20T10:00:00Z</updated><summary>Policy repo rate unchanged</summary></entry>
</feed>"""

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>
<rss><channel><item><title>&lol2;</title><link>https://x/y</link></item></channel></rss>"""


def test_rss_items_are_plain_text_with_zone_aware_times_and_ttl() -> None:
    f = parse_feed(RSS, max_items=50)
    assert f.ttl_minutes == 15
    assert f.skipped == 1  # the item without a link
    first, second = f.items
    assert first.title == "Sensex rises & Nifty gains"
    assert first.summary == "Stocks rose today."  # tags stripped, never stored as HTML
    assert first.published_at == datetime(2026, 11, 20, 10, 15, tzinfo=UTC)
    assert second.published_at is None  # an unreadable date is unknown, not guessed


def test_atom_entries_are_read() -> None:
    [item] = parse_feed(ATOM, max_items=50).items
    assert (item.title, item.link, item.summary) == (
        "Press release",
        "https://rbi.example/p1",
        "Policy repo rate unchanged",
    )


def test_entity_expansion_and_garbage_are_refused() -> None:
    with pytest.raises(FeedError):
        parse_feed(BILLION_LAUGHS, max_items=50)
    with pytest.raises(FeedError):
        parse_feed(b"<html><body>not a feed</body></html>", max_items=50)
    with pytest.raises(FeedError):
        parse_feed(b"not xml at all", max_items=50)


def test_feed_item_cap() -> None:
    assert len(parse_feed(RSS, max_items=1).items) == 1


def test_fred_values_are_exact_scaled_integers() -> None:
    body = {
        "observations": [
            {"date": "2026-11-19", "value": "5974.07"},
            {"date": "2026-11-20", "value": "."},  # no value that day: skipped, not zero
            {"date": "2026-11-21", "value": "5980.123"},  # more decimals than scale 2: rejected
            {"date": "2026-11-22", "value": "5980.000000000000000000000000001"},
            {"date": "2026-11-22", "value": "NaN"},
            {"date": "2026-11-22", "value": "Infinity"},
            {"date": "2026-11-22", "value": "92233720368547758.08"},
            {"date": "bad", "value": "1"},
        ]
    }
    obs, rejected = parse_observations(body, scale=2)
    assert [(o.obs_date, o.value_scaled) for o in obs] == [(date(2026, 11, 19), 597407)]
    assert rejected == 6
    with pytest.raises(FredError):
        parse_observations({"error": "bad key"}, scale=2)


CAL_OK = """event_date,event_time,timezone,country,kind,title,source_url
2026-12-05,10:00,Asia/Kolkata,IN,rbi_policy,RBI monetary policy decision,https://www.rbi.org.in/x
2026-12-16,,America/New_York,US,fomc,FOMC meeting (day 2),https://www.federalreserve.gov/y
"""


def test_calendar_csv_parses_with_sources() -> None:
    a, b = parse_calendar_csv(CAL_OK)
    assert (a.event_date, a.event_time, a.country, a.kind) == (
        date(2026, 12, 5),
        time(10, 0),
        "IN",
        "rbi_policy",
    )
    assert b.event_time is None


def test_calendar_import_is_all_or_nothing_with_every_problem_listed() -> None:
    bad = CAL_OK + (
        "2026-13-01,10:00,Asia/Kolkata,IN,cpi,CPI,https://mospi.gov.in/z\n"
        "2026-12-12,25:00,Asia/Kolkata,IN,cpi,CPI,https://mospi.gov.in/z\n"
        "2026-12-12,,Mars/Base,IN,cpi,CPI,https://mospi.gov.in/z\n"
        "2026-12-12,,Asia/Kolkata,IN,cpi,CPI,http://insecure.example/z\n"
    )
    with pytest.raises(CalendarImportError) as e:
        parse_calendar_csv(bad)
    assert len(e.value.problems) == 4
    with pytest.raises(CalendarImportError, match="header"):
        parse_calendar_csv("date,title\n2026-12-01,x\n")
