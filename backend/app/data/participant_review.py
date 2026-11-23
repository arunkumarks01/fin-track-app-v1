"""Preserve untrusted participant source bytes for review, never as clean data."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any


async def retain_participant_source(
    conn: Any, dataset: str, day: date, url: str, blob: bytes, error: str, now: datetime
) -> None:
    """Store the exact source and digest in the same transaction as its error ledger."""
    digest = hashlib.sha256(blob).hexdigest()
    await conn.execute(
        """INSERT INTO app.participant_source_review
           (dataset,trade_date,source_url,source_sha256,source_bytes,validation_error,captured_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7)
           ON CONFLICT (dataset,trade_date,source_sha256) DO NOTHING""",
        dataset,
        day,
        url,
        digest,
        blob,
        error[:500],
        now,
    )
