"""Research candidates from finalized candles and revisioned definitions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from app.analytics.chain import option_report
from app.analytics.technical import indicator_report
from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ
from app.risk.report import attach_plans
from app.scoring.calibration import conviction, model_key
from app.scoring.contracts import futures_candidate, option_candidate
from app.scoring.rules import BUILT_INS, Instrument, Mode, Strategy, Value, assess

_BUILT_IN_IDS = frozenset(strategy.id for strategy in BUILT_INS)


def context(row: dict[str, Any] | None, settings: Settings) -> dict[str, Value]:
    if row is None or row["values"] is None:
        return {}
    values: dict[str, Value] = dict(row["values"])
    low, mid, high = (
        values.get("bollinger_lower_paisa"),
        values.get("bollinger_middle_paisa"),
        values.get("bollinger_upper_paisa"),
    )
    values.update(
        bollinger_width_pct=100 * (high - low) / mid
        if low is not None and high is not None and mid
        else None,
        rsi_low=settings.scoring.rsi_low,
        rsi_high=settings.scoring.rsi_high,
        squeeze_width_pct=settings.scoring.squeeze_width_pct,
        gap_min_pct=settings.scoring.gap_min_pct,
        negative_gap_min_pct=-settings.scoring.gap_min_pct,
        momentum_min_relative_volume=settings.scoring.momentum_min_relative_volume,
    )
    return values


async def strategy_rows(conn: Any, as_of: datetime) -> list[Any]:
    rows = await conn.fetch(
        """SELECT id, revision, enabled, definition, created_at FROM (
             SELECT DISTINCT ON (id) id, revision, enabled, definition, created_at
             FROM app.scoring_strategies WHERE created_at <= $1 ORDER BY id, revision DESC
           ) AS latest ORDER BY id""",
        as_of,
    )
    return list(rows)


async def save_strategy(
    conn: Any, definition: Strategy, enabled: bool, created_at: datetime
) -> int | None:
    if definition.id in _BUILT_IN_IDS or definition.id.startswith(
        ("directional_option_", "expiry_gamma_")
    ):
        raise ValueError("That identifier belongs to the built-in catalogue")
    revision = await conn.fetchval(
        """INSERT INTO app.scoring_strategies (id, revision, created_at, enabled, definition)
           SELECT $1, $2, $3, $4, $5::jsonb
           WHERE $2 = 1 + coalesce(
               (SELECT max(revision) FROM app.scoring_strategies WHERE id = $1), 0)
           ON CONFLICT DO NOTHING RETURNING revision""",
        definition.id,
        definition.revision,
        created_at,
        enabled,
        definition.model_dump_json(),
    )
    return cast(int | None, revision)


async def candidate_report(
    conn: Any,
    symbol: str,
    mode: Mode,
    as_of: datetime,
    settings: Settings,
    instrument: Instrument,
    *,
    include_trade_plans: bool = False,
    paper_account_id: int | None = None,
) -> dict[str, Any]:
    market = await indicator_report(
        conn,
        symbol,
        "D" if mode == "swing" else "5m",
        as_of,
        min(2, settings.analytics_max_result_bars),
        settings,
        include_scoring_inputs=True,
    )
    rows = market["rows"]
    latest = rows[-1] if rows else None
    prior = rows[-2] if len(rows) > 1 else None
    if latest and prior and latest["usable_bars"] != prior["usable_bars"] + 1:
        prior = None
    if latest and prior and mode == "intraday":
        current_day = datetime.fromtimestamp(latest["t"], MARKET_TZ).date()
        if current_day != datetime.fromtimestamp(prior["t"], MARKET_TZ).date():
            prior = None
    current, previous = context(latest, settings), context(prior, settings)
    if mode == "intraday" and prior is None:
        current["close_change_paisa"] = None
    usable = bool(latest and latest["values"] is not None and not market["behind_benchmark"])
    if not usable:
        current, previous = {}, {}
    custom = [
        Strategy.model_validate_json(r["definition"])
        for r in await strategy_rows(conn, as_of)
        if r["enabled"]
    ]
    evaluations: list[dict[str, Any]] = [
        assess(strategy, current, previous, settings.shorts_enabled)
        for strategy in (*BUILT_INS, *custom)
        if strategy.mode == mode
    ]
    ticker = symbol.split(":", 1)[1]
    underlying = "NIFTY" if ticker == "NIFTY 50" else ticker
    nse = symbol.startswith("NSE:")
    trade_date = datetime.fromtimestamp(latest["t"], MARKET_TZ).date() if latest else None
    if instrument == "option":
        chain = (
            await option_report(
                conn,
                underlying,
                as_of,
                settings.analytics_option_rate_bps,
                settings.analytics_option_dividend_bps,
                settings,
            )
            if nse and usable
            else {}
        )
        if mode == "intraday" and trade_date != as_of.astimezone(MARKET_TZ).date():
            # An old technical signal cannot be combined with a current contract quote.
            chain = {}
        evaluations = []
        for kind in ("CE", "PE"):
            if mode == "swing":
                evaluations.append(option_candidate(chain, mode, kind, current, settings.scoring))
            else:
                evaluations.append(
                    option_candidate(
                        chain, mode, kind, current, settings.scoring, expiry_gamma=True
                    )
                )
    elif instrument == "future":
        future = (
            await conn.fetchrow(
                """SELECT trade_date, expiry, close_paisa, contracts, open_interest
                   FROM app.fo_daily
                   WHERE trade_date = $1 AND underlying = $2 AND kind IN ('IDF', 'STF')
                     AND expiry > trade_date AND contracts > 0
                   ORDER BY expiry ASC LIMIT 1""",
                trade_date,
                underlying,
            )
            if nse and usable and mode == "swing"
            else None
        )
        evaluations = [futures_candidate(item, future, settings.scoring) for item in evaluations]
    fundamentals = (
        await conn.fetchrow(
            """SELECT trade_date, market_cap_paisa, issue_size FROM app.market_cap_daily
           WHERE symbol = $1 AND trade_date <= $2 ORDER BY trade_date DESC LIMIT 1""",
            symbol,
            trade_date,
        )
        if trade_date
        else None
    )
    calibrations = {
        (r["strategy_id"], r["strategy_revision"], r["instrument"]): r
        for r in await conn.fetch(
            """SELECT DISTINCT ON (strategy_id, strategy_revision, instrument) *
               FROM app.scoring_calibrations WHERE available_at <= $1
               AND strategy_id = ANY($2::text[]) AND model_key = $3
               ORDER BY strategy_id, strategy_revision, instrument, available_at DESC""",
            as_of,
            [r["id"] for r in evaluations if r["status"] == "matched"],
            model_key(settings),
        )
    }
    for item in evaluations:
        item["instrument"] = instrument
        item["conviction"] = conviction(
            calibrations.get((item["id"], item["revision"], item["instrument"]))
            if item["status"] == "matched"
            else None,
            settings.scoring.min_calibration_observations,
        )
    if include_trade_plans and trade_date:
        await attach_plans(
            conn,
            evaluations,
            current,
            symbol,
            mode,
            instrument,
            trade_date,
            as_of,
            settings,
            paper_account_id,
        )
    return {
        "symbol": symbol,
        "mode": mode,
        "instrument": instrument,
        "interval": market["interval"],
        "source": market["source"],
        "as_of": as_of.astimezone(UTC).isoformat(),
        "latest_bar": latest["t"] if latest else None,
        "previous_bar": prior["t"] if prior else None,
        "latest_benchmark_session": market["latest_benchmark_session"],
        "data_status": "behind_benchmark" if market["behind_benchmark"] else market["status"],
        "fundamentals": {
            "trade_date": fundamentals["trade_date"].isoformat(),
            "market_cap_paisa": fundamentals["market_cap_paisa"],
            "issue_size": fundamentals["issue_size"],
        }
        if fundamentals
        else None,
        "evaluations": evaluations,
    }


def serialize_strategy(row: Any) -> dict[str, Any]:
    return {
        "definition": json.loads(row["definition"]),
        "enabled": row["enabled"],
        "created_at": row["created_at"].isoformat(),
    }
