"""Explicitly labelled research access to reviewed NSE participant source values.

This is separate from clean participant positions. Dates or component sums that cannot be
verified remain gaps. Exact values and both published and derived totals stay distinct.
"""

from __future__ import annotations

import json
from decimal import Decimal, localcontext
from typing import Any, cast

from app.data.nse_reference import _LONG, _SHORT, FIELDS
from app.data.participant_assessment import METHOD

_ALLOWED = {"reconciled_within_tolerance", "component_totals_disagree"}


def _derived(values: list[Decimal], indexes: tuple[int, ...]) -> str:
    # Decimal arithmetic must not round a long source coefficient or a fine fractional scale.
    precision = (
        max(len(v.as_tuple().digits) + abs(cast(int, v.as_tuple().exponent)) for v in values) + 20
    )
    with localcontext() as context:
        context.prec = max(precision, 28)
        return str(sum((values[i] for i in indexes), Decimal(0)))


async def reviewed_participants(conn: Any, tolerance: int) -> dict[str, Any]:
    """Return labelled source values only when date and component checks pass."""
    method = f"{METHOD}_tolerance_{tolerance}"
    files = await conn.fetch(
        """SELECT DISTINCT ON (q.dataset,q.trade_date)
                  q.dataset,q.trade_date,q.source_sha256,q.source_url,
                  x.date_status,x.fractional,x.source_differences,d.outcome
           FROM app.participant_source_review q
           JOIN app.reference_files f USING (dataset,trade_date)
           LEFT JOIN app.participant_exact_imports x USING (dataset,trade_date,source_sha256)
           LEFT JOIN app.participant_review_decisions d
             ON d.dataset=q.dataset AND d.trade_date=q.trade_date
                AND d.source_sha256=q.source_sha256 AND d.method=$1
           WHERE f.status <> 'ok'
           ORDER BY q.dataset,q.trade_date,q.captured_at DESC,q.source_sha256 DESC""",
        method,
    )
    rows = await conn.fetch(
        "SELECT dataset,trade_date,source_sha256,client_type,values_exact "
        "FROM app.participant_exact_rows ORDER BY dataset,trade_date,client_type"
    )
    missing = await conn.fetch(
        "SELECT dataset,trade_date FROM app.reference_files "
        "WHERE dataset=ANY($1::text[]) AND status='missing' ORDER BY dataset,trade_date",
        ["participant_oi", "participant_vol"],
    )
    by_source: dict[tuple[Any, ...], list[Any]] = {}
    for row in rows:
        key = (row["dataset"], row["trade_date"], row["source_sha256"])
        by_source.setdefault(key, []).append(row)
    usable: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for file in files:
        key = (file["dataset"], file["trade_date"], file["source_sha256"])
        outcome = file["outcome"]
        values = by_source.get(key, [])
        if (
            outcome not in _ALLOWED
            or file["date_status"] != "verified"
            or {r["client_type"] for r in values} != {"Client", "DII", "FII", "Pro", "TOTAL"}
        ):
            gaps.append(
                {
                    "dataset": file["dataset"],
                    "candidate_date": file["trade_date"].isoformat(),
                    "reason": outcome or "assessment_unavailable",
                }
            )
            continue
        source_discrepancies = json.loads(file["source_differences"])
        items = []
        for row in values:
            numbers = list(row["values_exact"])
            if len(numbers) != len(FIELDS) or any(not n.is_finite() or n < 0 for n in numbers):
                raise ValueError("Invalid exact participant source values")
            items.append(
                {
                    "participant": row["client_type"],
                    "components": {field: str(numbers[i]) for i, field in enumerate(FIELDS[:12])},
                    "derived_totals": {
                        "long": _derived(numbers, _LONG),
                        "short": _derived(numbers, _SHORT),
                    },
                    "reported_totals": {"long": str(numbers[12]), "short": str(numbers[13])},
                }
            )
        if outcome == "component_totals_disagree":
            warning = (
                "NSE printed totals disagree with component sums; "
                "derived totals are calculated separately."
            )
        elif file["fractional"]:
            warning = (
                "NSE reported fractional values; totals reconcile within the configured tolerance."
            )
        else:
            warning = "NSE totals reconcile within the configured tolerance."
        usable.append(
            {
                "dataset": file["dataset"],
                "date": file["trade_date"].isoformat(),
                "source": "NSE",
                "source_url": file["source_url"],
                "source_sha256": file["source_sha256"],
                "quality": outcome,
                "fractional": file["fractional"],
                "warning": warning,
                "tolerance_contracts": tolerance,
                "source_total_differences": source_discrepancies,
                "participants": items,
            }
        )
    known_gaps = {(g["dataset"], g["candidate_date"]) for g in gaps}
    for missing_file in missing:
        missing_key = (missing_file["dataset"], missing_file["trade_date"].isoformat())
        if missing_key not in known_gaps:
            gaps.append(
                {
                    "dataset": missing_key[0],
                    "candidate_date": missing_key[1],
                    "reason": "missing_or_mislabeled_source",
                }
            )
    return {"scope": "labelled_research_only", "files": usable, "gaps": gaps}
