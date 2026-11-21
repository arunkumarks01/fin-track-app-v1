"""FRED (St. Louis Fed) daily series — US markets and macro for the pre-open picture (1e).

Off until the user puts FRED_API_KEY in .env (fail closed: a clear 'no key' status, nothing
fetched). The key travels only in the request; it is a registered secret, so the log masker
removes it from any message. Values are stored as exact scaled integers (value * 10^scale);
a value with more decimals than its scale is rejected, never rounded. FRED marks a missing
day with "." — skipped, not stored as zero. Each run fetches only observations after the
newest stored one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

import httpx

from app.core.clock import Clock
from app.core.config import Settings

log = logging.getLogger(__name__)


class FredError(RuntimeError):
    pass


@dataclass(frozen=True)
class Observation:
    obs_date: date
    value_scaled: int


def parse_observations(body: Any, scale: int) -> tuple[list[Observation], int]:
    """(observations, rejected) from FRED's JSON. '.' = no value that day (skipped)."""
    if not isinstance(body, dict) or not isinstance(body.get("observations"), list):
        raise FredError("unexpected FRED response shape")
    out: list[Observation] = []
    rejected = 0
    for o in body["observations"]:
        try:
            d = date.fromisoformat(o["date"])
            raw = str(o["value"]).strip()
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        if raw == ".":
            continue
        try:
            value = Decimal(raw)
            if not value.is_finite() or (value and value.adjusted() + scale > 18):
                rejected += 1
                continue
            with localcontext() as ctx:
                ctx.prec = max(ctx.prec, len(value.as_tuple().digits))
                ctx.traps[InvalidOperation] = True
                scaled = value.scaleb(scale)
        except InvalidOperation:
            rejected += 1
            continue
        if (
            scaled != scaled.to_integral_value()
            or not -(2**63) <= scaled < 2**63
            or (value and not scaled)
        ):
            rejected += 1
            continue
        out.append(Observation(d, int(scaled)))
    return out, rejected


@dataclass
class FredReport:
    started_at: datetime
    series: dict[str, str] = field(default_factory=dict)
    stored: int = 0
    rejected: int = 0
    stopped_reason: str | None = None

    def totals(self) -> dict[str, Any]:
        return {
            "series": self.series,
            "stored": self.stored,
            "rejected": self.rejected,
            "inserted": self.stored,
            "quarantined": self.rejected,
        }


async def fetch_fred(
    pool: Any, http: httpx.AsyncClient, settings: Settings, clock: Clock
) -> FredReport:
    report = FredReport(started_at=clock.now())
    if settings.fred_api_key is None:
        report.stopped_reason = "FRED_API_KEY is not set — US market series are not loaded"
        return report
    key = settings.fred_api_key.get_secret_value()
    async with pool.acquire() as conn:
        await conn.executemany(
            """INSERT INTO app.macro_series_meta (series_id, title, unit, scale)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (series_id) DO UPDATE SET title = EXCLUDED.title""",
            [(sid, t, u, sc) for sid, (t, u, sc) in settings.fred_series.items()],
        )
        scales = {
            r["series_id"]: (r["scale"], r["unit"])
            for r in await conn.fetch("SELECT series_id, scale, unit FROM app.macro_series_meta")
        }
        newest = {
            r["series_id"]: r["d"]
            for r in await conn.fetch(
                "SELECT series_id, max(obs_date) AS d FROM app.macro_series GROUP BY 1"
            )
        }
    for sid, (_, unit, scale) in settings.fred_series.items():
        stored_scale, stored_unit = scales[sid]
        if (stored_scale, stored_unit) != (scale, unit):
            # changing a series' scale would make stored integers mean something else
            report.series[sid] = "error"
            await _status(pool, sid, "error", "scale/unit changed from the stored one", clock)
            continue
        start = (newest[sid] + timedelta(days=1)) if sid in newest else settings.fred_start
        try:
            resp = await http.get(
                f"{settings.fred_base_url.rstrip('/')}/series/observations",
                params={
                    "series_id": sid,
                    "api_key": key,
                    "file_type": "json",
                    "observation_start": start.isoformat(),
                },
                timeout=30.0,
            )
        except httpx.TransportError as exc:
            report.series[sid] = "error"
            await _status(pool, sid, "error", f"unreachable: {type(exc).__name__}", clock)
            continue
        if resp.status_code in (401, 403):
            report.series[sid] = "refused"
            report.stopped_reason = f"FRED refused ({resp.status_code}) — check the API key"
            await _status(pool, sid, "refused", f"HTTP {resp.status_code}", clock)
            break
        if resp.status_code != 200:
            report.series[sid] = "error"
            await _status(pool, sid, "error", f"HTTP {resp.status_code}", clock)
            continue
        try:
            obs, rejected = parse_observations(resp.json(), scale)
        except (FredError, ValueError) as exc:
            report.series[sid] = "error"
            await _status(pool, sid, "error", str(exc), clock)
            continue
        async with pool.acquire() as conn, conn.transaction():
            if obs:
                await conn.executemany(
                    """INSERT INTO app.macro_series (series_id, obs_date, value_scaled)
                       VALUES ($1, $2, $3) ON CONFLICT (series_id, obs_date) DO NOTHING""",
                    [(sid, o.obs_date, o.value_scaled) for o in obs],
                )
            await conn.execute(
                """UPDATE app.macro_series_meta SET last_status = 'ok', last_detail = $2,
                       last_fetched_at = $3 WHERE series_id = $1""",
                sid,
                f"{len(obs)} new, {rejected} rejected" if rejected else f"{len(obs)} new",
                clock.now(),
            )
        report.series[sid] = "ok"
        report.stored += len(obs)
        report.rejected += rejected
    return report


async def _status(pool: Any, sid: str, status: str, detail: str, clock: Clock) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE app.macro_series_meta SET last_status = $2, last_detail = $3,
                   last_fetched_at = $4 WHERE series_id = $1""",
            sid,
            status,
            detail[:500],
            clock.now(),
        )
