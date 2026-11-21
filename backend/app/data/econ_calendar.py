"""Economic calendar: imported from a reviewed CSV, never from dates written in code (1e).

CSV columns (header required, in this order):
  event_date,event_time,timezone,country,kind,title,source_url
- event_date  YYYY-MM-DD
- event_time  HH:MM (24h, local to `timezone`) or empty when the publisher gives no time
- timezone    an IANA name, e.g. Asia/Kolkata, America/New_York
- country     two letters, e.g. IN, US
- kind        lowercase word(s): rbi_policy, cpi, iip, gdp, fomc, ...
- title       free text
- source_url  https:// page the date was read from (required on every row)
The import is all-or-nothing: one bad row rejects the whole file, with every problem listed.
Re-importing the same row is a no-op (unique on date, country, kind, title).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

COLUMNS = ("event_date", "event_time", "timezone", "country", "kind", "title", "source_url")
_KIND = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


class CalendarImportError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems[:20]))
        self.problems = problems


@dataclass(frozen=True)
class EconEvent:
    event_date: date
    event_time: time | None
    timezone: str
    country: str
    kind: str
    title: str
    source_url: str


def parse_calendar_csv(text: str) -> list[EconEvent]:
    try:
        rows = list(csv.reader(io.StringIO(text.removeprefix("\ufeff")), strict=True))
    except csv.Error as exc:
        raise CalendarImportError([f"invalid CSV: {exc}"]) from exc
    if not rows or tuple(c.strip() for c in rows[0]) != COLUMNS:
        raise CalendarImportError([f"header must be exactly: {','.join(COLUMNS)}"])
    out: list[EconEvent] = []
    problems: list[str] = []
    for n, r in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in r):
            continue
        if len(r) != len(COLUMNS):
            problems.append(f"line {n}: {len(r)} fields, expected {len(COLUMNS)}")
            continue
        d, t, tz, country, kind, title, url = (c.strip() for c in r)
        try:
            ev_date = date.fromisoformat(d)
        except ValueError:
            problems.append(f"line {n}: bad date {d!r}")
            continue
        ev_time = None
        if t:
            try:
                ev_time = datetime.strptime(t, "%H:%M").time()  # noqa: DTZ007 — a wall time
            except ValueError:
                problems.append(f"line {n}: bad time {t!r} (HH:MM)")
                continue
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            problems.append(f"line {n}: unknown timezone {tz!r}")
            continue
        if not re.fullmatch(r"[A-Z]{2}", country):
            problems.append(f"line {n}: country must be two capital letters, got {country!r}")
            continue
        if not _KIND.fullmatch(kind):
            problems.append(f"line {n}: kind must be lowercase letters/digits/_ , got {kind!r}")
            continue
        if not title:
            problems.append(f"line {n}: empty title")
            continue
        if not url.startswith("https://"):
            problems.append(f"line {n}: source_url must be an https:// link")
            continue
        out.append(EconEvent(ev_date, ev_time, tz, country, kind, title[:300], url[:1000]))
    if problems:
        raise CalendarImportError(problems)
    if not out:
        raise CalendarImportError(["no events in the file"])
    return out


async def import_calendar(conn: Any, events: list[EconEvent], now: datetime) -> int:
    """Insert new events (caller holds the transaction). Returns how many were new."""
    inserted = await conn.fetchmany(
        """INSERT INTO app.economic_events
               (event_date, event_time, timezone, country, kind, title, source_url, imported_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           ON CONFLICT (event_date, country, kind, title) DO NOTHING RETURNING 1""",
        [
            (e.event_date, e.event_time, e.timezone, e.country, e.kind, e.title, e.source_url, now)
            for e in events
        ],
    )
    return len(inserted)


async def upcoming(conn: Any, start: date, end: date) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT event_date, event_time, timezone, country, kind, title, source_url
           FROM app.economic_events WHERE event_date BETWEEN $1 AND $2
           ORDER BY event_date, event_time NULLS LAST, country""",
        start,
        end,
    )
    return [
        {
            "date": r["event_date"].isoformat(),
            "time": r["event_time"].strftime("%H:%M") if r["event_time"] else None,
            "timezone": r["timezone"],
            "country": r["country"],
            "kind": r["kind"],
            "title": r["title"],
            "source_url": r["source_url"],
        }
        for r in rows
    ]
