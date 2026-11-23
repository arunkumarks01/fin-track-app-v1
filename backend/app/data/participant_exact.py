"""Persist exact NSE source observations in a separate review-only dataset."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.data.nse_reference import FIELDS
from app.data.participant_diagnostics import analyze_source


async def import_archived(conn: Any, now: datetime) -> dict[str, Any]:
    """Import each unverified source atomically, without changing its original ledger."""
    sources = await conn.fetch(
        """SELECT q.dataset,q.trade_date,q.source_sha256,q.source_bytes
           FROM app.participant_source_review q
           JOIN app.reference_files f USING (dataset,trade_date)
           WHERE f.status <> 'ok' ORDER BY q.dataset,q.trade_date,q.source_sha256"""
    )
    inserted = existing = 0
    failures: list[dict[str, str]] = []
    for source in sources:
        dataset, day, digest = source["dataset"], source["trade_date"], source["source_sha256"]
        blob = bytes(source["source_bytes"])
        try:
            if hashlib.sha256(blob).hexdigest() != digest:
                raise ValueError("Archived source checksum mismatch")
            analysis = analyze_source(blob, day, "oi" if dataset == "participant_oi" else "volume")
            async with conn.transaction():
                created = await conn.fetchval(
                    """INSERT INTO app.participant_exact_imports
                       (dataset,trade_date,source_sha256,date_status,fractional,source_differences,imported_at)
                       VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7)
                       ON CONFLICT DO NOTHING RETURNING true""",
                    dataset,
                    day,
                    digest,
                    analysis["date_status"],
                    analysis["has_fractional_values"],
                    json.dumps(analysis["source_total_differences"]),
                    now,
                )
                if created:
                    await conn.executemany(
                        """INSERT INTO app.participant_exact_rows
                           (dataset,trade_date,source_sha256,client_type,values_exact)
                           VALUES ($1,$2,$3,$4,$5)""",
                        [
                            (
                                dataset,
                                day,
                                digest,
                                who,
                                [Decimal(values[field]) for field in FIELDS],
                            )
                            for who, values in analysis["rows"].items()
                        ],
                    )
        except (ValueError, UnicodeError) as exc:
            failures.append({"dataset": dataset, "date": day.isoformat(), "error": str(exc)})
        else:
            if created:
                inserted += 1
            else:
                existing += 1
    return {
        "sources": len(sources),
        "inserted": inserted,
        "already_imported": existing,
        "failures": failures,
        "status": "review_required",
    }
