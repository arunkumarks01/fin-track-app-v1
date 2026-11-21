"""News headlines from publisher RSS / Atom feeds (Phase 1e, DEC-1E-SOURCES).

Headline, link and a plain-text summary only — never the article. XML is parsed with
defusedxml (untrusted input: no entity expansion, no external DTDs). Each feed is fetched with
an honest client name; a 401/403 marks that feed 'refused' and it is not retried with other
headers. A feed's own <ttl> (minutes) is respected: it is not fetched again sooner. Items are
deduplicated on their link. Classification is the Phase-3 news agent's job, not this module's.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import defusedxml.ElementTree as SafeET
import httpx
from defusedxml import DefusedXmlException

from app.core.clock import Clock
from app.core.config import Settings

log = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")
_ATOM = "{http://www.w3.org/2005/Atom}"


class FeedError(ValueError):
    """The feed could not be read as RSS/Atom — never guessed."""


@dataclass(frozen=True)
class NewsItem:
    title: str
    link: str
    summary: str | None
    published_at: datetime | None


@dataclass(frozen=True)
class Feed:
    items: list[NewsItem]
    ttl_minutes: int | None
    skipped: int  # items without a title or an http(s) link


def _text(v: str | None, limit: int) -> str | None:
    if v is None:
        return None
    t = " ".join(html.unescape(_TAG.sub(" ", v)).split())
    return t[:limit] or None


def _when(v: str | None) -> datetime | None:
    if not v or not v.strip():
        return None
    s = v.strip()
    try:
        d = parsedate_to_datetime(s)  # RSS: RFC 822
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))  # Atom: RFC 3339
        except ValueError:
            return None
    return d if d.tzinfo else None  # a time without a zone is not trusted as an instant


def _find(e: Any, tag: str) -> str | None:
    v = e.findtext(tag)
    return v if isinstance(v, str) else None


def parse_feed(blob: bytes, max_items: int) -> Feed:
    try:
        root = SafeET.fromstring(blob)
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise FeedError(f"not readable XML: {type(exc).__name__}") from exc
    ttl: int | None = None
    rows: list[tuple[str | None, str | None, str | None, str | None]] = []  # title, link, when, sum
    channel = root.find("channel")
    if root.tag == "rss" or channel is not None:
        if channel is None:
            raise FeedError("RSS without a channel")
        t = _find(channel, "ttl")
        ttl = int(t) if t and t.strip().isdigit() else None
        for e in channel.findall("item"):
            rows.append(
                (_find(e, "title"), _find(e, "link"), _find(e, "pubDate"), _find(e, "description"))
            )
    elif root.tag == f"{_ATOM}feed":
        for e in root.findall(f"{_ATOM}entry"):
            node = e.find(f"{_ATOM}link[@rel='alternate']")
            if node is None:
                node = e.find(f"{_ATOM}link")
            href = node.get("href") if node is not None else None
            when = _find(e, f"{_ATOM}updated") or _find(e, f"{_ATOM}published")
            rows.append((_find(e, f"{_ATOM}title"), href, when, _find(e, f"{_ATOM}summary")))
    else:
        raise FeedError(f"neither RSS nor Atom (root {root.tag!r})")
    items: list[NewsItem] = []
    skipped = 0
    for raw_title, raw_link, when, summary in rows:
        title = _text(raw_title, 500)
        link = (raw_link or "").strip()
        if not title or not link.startswith(("https://", "http://")):
            skipped += 1
            continue
        items.append(NewsItem(title, link[:1000], _text(summary, 1000), _when(when)))
        if len(items) >= max_items:
            break
    return Feed(items, ttl, skipped)


@dataclass
class NewsReport:
    started_at: datetime
    feeds: dict[str, str] = field(default_factory=dict)  # source → ok/error/refused/skipped
    new_items: int = 0

    def totals(self) -> dict[str, Any]:
        return {"feeds": self.feeds, "new_items": self.new_items, "inserted": self.new_items}


async def fetch_news(
    pool: Any, http: httpx.AsyncClient, settings: Settings, clock: Clock
) -> NewsReport:
    now = clock.now()
    report = NewsReport(started_at=now)
    async with pool.acquire() as conn:
        await conn.executemany(
            """INSERT INTO app.news_feeds (source, url) VALUES ($1, $2)
               ON CONFLICT (source) DO UPDATE SET url = EXCLUDED.url""",
            list(settings.news_feeds.items()),
        )
        state = {
            r["source"]: r
            for r in await conn.fetch(
                "SELECT source, last_status, last_fetched_at, ttl_minutes FROM app.news_feeds"
            )
        }
    for source, url in settings.news_feeds.items():
        st = state.get(source)
        if st and st["last_status"] == "refused":
            report.feeds[source] = "refused (not retried)"
            continue
        if (
            st
            and st["last_fetched_at"]
            and st["ttl_minutes"]
            and now < st["last_fetched_at"] + timedelta(minutes=st["ttl_minutes"])
        ):
            report.feeds[source] = "skipped (feed ttl)"
            continue
        status, detail, new = await _one(pool, http, settings, source, url, now)
        report.feeds[source] = status
        report.new_items += new
        if status != "ok":
            log.warning("news %s: %s %s", source, status, detail)
    return report


async def _one(
    pool: Any, http: httpx.AsyncClient, settings: Settings, source: str, url: str, now: datetime
) -> tuple[str, str | None, int]:
    status, detail, feed = "ok", None, None
    try:
        resp = await http.get(
            url,
            headers={"User-Agent": settings.news_user_agent},
            timeout=settings.news_timeout_seconds,
            follow_redirects=True,
        )
        if resp.status_code in (401, 403):
            status, detail = "refused", f"HTTP {resp.status_code}"
        elif resp.status_code != 200:
            status, detail = "error", f"HTTP {resp.status_code}"
        elif len(resp.content) > settings.news_max_bytes:
            status, detail = "error", f"feed too large: {len(resp.content)} bytes"
        else:
            feed = parse_feed(resp.content, settings.news_max_items_per_feed)
            if feed.skipped:
                detail = f"{feed.skipped} items without a title or link skipped"
    except httpx.TransportError as exc:
        status, detail = "error", f"unreachable: {type(exc).__name__}"
    except FeedError as exc:
        status, detail = "error", str(exc)
    new = 0
    async with pool.acquire() as conn, conn.transaction():
        if feed is not None and feed.items:
            before = await conn.fetchval("SELECT count(*) FROM app.news_items")
            await conn.executemany(
                """INSERT INTO app.news_items (source, link, title, summary, published_at,
                                               fetched_at)
                   VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (link) DO NOTHING""",
                [(source, i.link, i.title, i.summary, i.published_at, now) for i in feed.items],
            )
            new = await conn.fetchval("SELECT count(*) FROM app.news_items") - before
        await conn.execute(
            """UPDATE app.news_feeds SET last_status = $2, last_detail = $3, last_fetched_at = $4,
                   items_last = $5, ttl_minutes = $6 WHERE source = $1""",
            source,
            status,
            detail and detail[:500],
            now,
            len(feed.items) if feed else 0,
            feed.ttl_minutes if feed else None,
        )
    return status, detail, new
