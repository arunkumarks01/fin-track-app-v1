"""Read-only exact-value diagnostics; never promote a source to clean analytics."""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.data.nse_reference import (
    _AS_ON,
    _LONG,
    _PREFIX_YEAR,
    _SHORT,
    _TITLE,
    CLIENT_TYPES,
    FIELDS,
    PARTICIPANT_COLUMNS,
)

_COUNT = re.compile(
    r"(?:\d+|\d{1,3}(?:,\d{3})+|\d{1,3}(?:,\d{2})+,\d{3})(?:\.\d+)?\Z",
    re.ASCII,
)


def analyze_source(blob: bytes, day: date, kind: str) -> dict[str, Any]:
    """Analyze exact published values; do not infer missing dates or correct totals."""
    if kind not in _TITLE:
        raise ValueError("Unknown participant kind")
    text = blob.decode("utf-8", errors="strict")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Empty source")
    title = lines[0].strip().rstrip(",").strip().strip('"')
    title = re.sub(r'(\b\d{4}),\s*"?(\d{4})"?$', lambda m: m[1] if m[1] == m[2] else m[0], title)
    match = _AS_ON.search(title)
    prefix = _PREFIX_YEAR.match(title)
    year = match and match["year"]
    if not year and prefix is not None:
        year = prefix[1]
    date_status = "unverified"
    if _TITLE[kind] in title.lower() and match is not None and year:
        if prefix is not None and year != prefix[1]:
            date_status = "conflicting"
        else:
            try:
                stated = datetime.strptime(  # noqa: DTZ007 — source is a calendar date
                    f"{match['month']} {match['day']} {year}", "%b %d %Y"
                ).date()
            except ValueError:
                date_status = "invalid"
            else:
                date_status = "verified" if stated == day else "mismatch"
    rows = list(csv.reader(io.StringIO(text, newline="")))
    header = next(
        (
            i
            for i, row in enumerate(rows)
            if tuple(c.strip() for c in row if c.strip()) == ("Client Type", *PARTICIPANT_COLUMNS)
        ),
        None,
    )
    if header is None:
        raise ValueError("Participant columns missing or changed")
    found: dict[str, tuple[Decimal, ...]] = {}
    for row in rows[header + 1 :]:
        cells = [c.strip() for c in row]
        while cells and not cells[-1]:
            cells.pop()
        if not cells:
            continue
        who = cells[0]
        if not who and "TOTAL" in found:
            continue
        if who not in (*CLIENT_TYPES, "TOTAL") or who in found:
            raise ValueError("Unexpected or duplicate participant row")
        if len(cells) not in (1 + len(FIELDS), 2 + len(FIELDS)):
            raise ValueError("Unexpected participant field count")
        raw_values = cells[1 : 1 + len(FIELDS)]
        if any(_COUNT.fullmatch(v) is None for v in raw_values):
            raise ValueError("Invalid decimal value")
        parsed = tuple(Decimal(v.replace(",", "")) for v in raw_values)
        if len(cells) == 2 + len(FIELDS):
            extra = cells[-1]
            if _COUNT.fullmatch(extra) is None or Decimal(extra.replace(",", "")) != sum(
                parsed[-2:]
            ):
                raise ValueError("Unexpected extra participant field")
        found[who] = parsed
    if set(found) != {*CLIENT_TYPES, "TOTAL"}:
        raise ValueError("Participant rows missing")
    discrepancies: dict[str, str] = {}
    for who, values in found.items():
        for label, indexes, total_index in (("long", _LONG, 12), ("short", _SHORT, 13)):
            diff = values[total_index] - sum((values[i] for i in indexes), Decimal(0))
            if diff:
                discrepancies[f"{who}.{label}"] = str(diff)
    for i, field in enumerate(FIELDS):
        diff = found["TOTAL"][i] - sum((found[who][i] for who in CLIENT_TYPES), Decimal(0))
        if diff:
            discrepancies[f"TOTAL.{field}"] = str(diff)
    return {
        "date_status": date_status,
        "has_fractional_values": any(
            v != v.to_integral_value() for row in found.values() for v in row
        ),
        "source_total_differences": discrepancies,
        "rows": {
            who: {field: str(value) for field, value in zip(FIELDS, values, strict=True)}
            for who, values in found.items()
        },
    }
