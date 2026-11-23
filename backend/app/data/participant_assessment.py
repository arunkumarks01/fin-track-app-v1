"""Reproducible review of preserved participant source values, never a correction."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.data.nse_reference import FIELDS

METHOD = "source_checks_v2"


def assess_source(date_status: str, differences: dict[str, str], tolerance: int) -> dict[str, Any]:
    """Separate source-date, component-sum and printed-total issues.

    Differences are NSE's published total less the sum of published components. A
    reconciled result is not a statement that fractional contracts are exchange-verified.
    """
    if tolerance < 0:
        raise ValueError("Tolerance cannot be negative")
    try:
        parsed = {key: Decimal(value) for key, value in differences.items()}
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Invalid source discrepancy") from exc
    if any(not value.is_finite() for value in parsed.values()):
        raise ValueError("Invalid source discrepancy")
    component_keys = {f"TOTAL.{field}" for field in FIELDS}
    large_components = {
        k: str(v) for k, v in parsed.items() if k in component_keys and abs(v) > tolerance
    }
    large_totals = {
        k: str(v) for k, v in parsed.items() if k not in component_keys and abs(v) > tolerance
    }
    if date_status != "verified":
        outcome = "source_date_unverified"
    elif large_components:
        outcome = "component_sums_disagree"
    elif large_totals:
        outcome = "component_totals_disagree"
    else:
        outcome = "reconciled_within_tolerance"
    return {
        "outcome": outcome,
        "date_status": date_status,
        "large_component_differences": large_components,
        "large_printed_total_differences": large_totals,
        "source_differences": differences,
    }


async def assess_imported(conn: Any, tolerance: int, now: datetime) -> dict[str, Any]:
    """Store an auditable automated review for each retained exact-value source."""
    method = f"{METHOD}_tolerance_{tolerance}"
    rows = await conn.fetch(
        """SELECT dataset,trade_date,source_sha256,date_status,source_differences
           FROM app.participant_exact_imports ORDER BY dataset,trade_date,source_sha256"""
    )
    counts: dict[str, int] = {}
    async with conn.transaction():
        for row in rows:
            outcome = assess_source(
                row["date_status"], json.loads(row["source_differences"]), tolerance
            )
            await conn.execute(
                """INSERT INTO app.participant_review_decisions
                   (dataset,trade_date,source_sha256,method,outcome,tolerance_contracts,detail,reviewed_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8)
                   ON CONFLICT (dataset,trade_date,source_sha256,method) DO NOTHING""",
                row["dataset"],
                row["trade_date"],
                row["source_sha256"],
                method,
                outcome["outcome"],
                Decimal(tolerance),
                json.dumps(outcome),
                now,
            )
            name = str(outcome["outcome"])
            counts[name] = counts.get(name, 0) + 1
    return {"method": method, "files": len(rows), "outcomes": counts, "tolerance": tolerance}
