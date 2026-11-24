"""Verified-history studies with no broker or messaging dependencies."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any

from app.backtest.engine import run_daily_long
from app.backtest.history import verified_daily_history
from app.backtest.metrics import performance
from app.backtest.signals import SIGNAL_THRESHOLDS, materialise_daily_long_signals
from app.backtest.store import create_run, save_result
from app.backtest.strategies import daily_long_spec
from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ, canonical_daily_ts
from app.scoring.rules import BUILT_INS


async def run_study(
    conn: Any,
    *,
    identifier: str,
    symbol: str,
    start_day: date,
    end_day: date,
    capital_paisa: int,
    quantity: int,
    settings: Settings,
    now: datetime,
) -> int:
    """Persist inputs first; insufficient or discontinuous coverage fails explicitly."""
    spec = daily_long_spec(identifier)
    if end_day < start_day or end_day >= now.astimezone(MARKET_TZ).date():
        raise ValueError("Use a historical range ending before today")
    if (end_day - start_day).days > 3660:
        raise ValueError("Study range cannot exceed ten years")
    if capital_paisa <= 0 or not 1 <= quantity <= 1_000_000:
        raise ValueError("Positive capital and quantity up to 1,000,000 required")
    assumptions: dict[str, Any] = {
        "engine_revision": 1,
        "signal_definition": next(s.model_dump() for s in BUILT_INS if s.id == spec.strategy_id),
        "signal_thresholds": SIGNAL_THRESHOLDS,
        "spec": asdict(spec),
        "paper": settings.paper.model_dump(),
        "periods": settings.analytics_periods.model_dump(),
        "quantity": quantity,
        "warmup_requested": settings.analytics_warmup_bars,
        "entry": "previous final signal -> next session open plus adverse slippage",
        "exits": "entry-day protection; stop first on double touch; trail effective next session",
        "time_exit": "open after max_holding_sessions completed sessions",
        "liquidity": "shared daily participation budget; partial exit remainder at next opens",
        "price_basis": (
            "NSE as-traded; price return excludes dividends; non-dividend actions rejected"
        ),
        "end_policy": "open positions marked at final close, no forced sale",
        "slippage": "paper adverse rounding; no favourable clipping to later daily high/low",
    }
    run_id = await create_run(
        conn,
        strategy_id=spec.strategy_id,
        revision=spec.revision,
        symbol=symbol,
        start_day=start_day,
        end_day=end_day,
        capital_paisa=capital_paisa,
        assumptions=assumptions,
        now=now,
    )
    await conn.execute("UPDATE app.backtest_runs SET status='running' WHERE id=$1", run_id)
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            history_start = start_day - timedelta(days=3 * settings.analytics_warmup_bars + 30)
            bars, benchmark = await verified_daily_history(conn, symbol, history_start, end_day)
            warm = [b for b in bars if datetime.fromtimestamp(b.t, MARKET_TZ).date() < start_day]
            warm = warm[-settings.analytics_warmup_bars :] if settings.analytics_warmup_bars else []
            study = [b for b in bars if datetime.fromtimestamp(b.t, MARKET_TZ).date() >= start_day]
            minimum = settings.analytics_periods.minimum_history()
            if len(warm) < minimum or not study:
                raise ValueError(
                    f"Insufficient verified history: need {minimum} warm-up bars and study sessions"
                )
            bars = tuple(warm + study)
            first = datetime.fromtimestamp(bars[0].t, MARKET_TZ).date()
            expected = {t for t in benchmark if bars[0].t <= t}
            if expected != {b.t for b in bars}:
                raise ValueError(
                    "Incomplete equity/NIFTY session alignment; refusing to skip sessions"
                )
            if int(canonical_daily_ts(end_day, MARKET_TZ).timestamp()) not in expected:
                raise ValueError("End date must be a verified NIFTY trading session")
            archive_days = await conn.fetch(
                """SELECT trade_date,status FROM app.nse_archive_files
                   WHERE trade_date BETWEEN $1 AND $2""",
                first,
                end_day,
            )
            archive_ts = {
                int(canonical_daily_ts(row["trade_date"], MARKET_TZ).timestamp())
                for row in archive_days
                if row["status"] == "ok"
            }
            if archive_ts != expected or any(row["status"] != "ok" for row in archive_days):
                raise ValueError("Incomplete verified NSE archive/NIFTY session alignment")
            actions = await conn.fetchval(
                """SELECT count(*) FROM app.corporate_actions
                   WHERE symbol=$1 AND ex_date BETWEEN $2 AND $3
                     AND kinds <> ARRAY['dividend']::text[]""",
                symbol,
                first,
                end_day,
            )
            if actions:
                raise ValueError(
                    "Non-dividend corporate actions require share/price adjustment support"
                )
        snapshot = [[b.t, b.open, b.high, b.low, b.close, b.volume, benchmark[b.t]] for b in bars]
        signals = await asyncio.to_thread(
            materialise_daily_long_signals, bars, benchmark, settings.analytics_periods
        )
        assumptions.update(
            input_sha256=hashlib.sha256(
                json.dumps(snapshot, separators=(",", ":")).encode()
            ).hexdigest(),
            warmup_bars=len(warm),
            study_bars=len(study),
            input_first_day=str(first),
            input_last_day=str(datetime.fromtimestamp(bars[-1].t, MARKET_TZ).date()),
            signal_count=sum(
                s.strategy_id == spec.strategy_id and s.timestamp >= study[0].t for s in signals
            ),
        )
        await conn.execute(
            "UPDATE app.backtest_runs SET assumptions=$2::jsonb WHERE id=$1",
            run_id,
            json.dumps(assumptions, sort_keys=True),
        )
        result = await asyncio.to_thread(
            run_daily_long,
            bars,
            signals,
            spec,
            symbol,
            start_day,
            capital_paisa,
            quantity,
            settings.paper,
        )
        await save_result(conn, run_id, result, performance(result, capital_paisa))
    except asyncio.CancelledError:
        await conn.execute(
            """UPDATE app.backtest_runs SET status='failed',detail='Study cancelled; rerun required'
               WHERE id=$1 AND status='running'""",
            run_id,
        )
        raise
    except Exception as exc:
        await conn.execute(
            """UPDATE app.backtest_runs SET status='failed',detail=$2
               WHERE id=$1 AND status='running'""",
            run_id,
            str(exc)[:1000],
        )
    return run_id
