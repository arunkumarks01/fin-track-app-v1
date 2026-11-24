"""Run all five studies explicitly: uv run python -m app.backtest --help."""

import argparse
import asyncio
import json
from datetime import UTC, date, datetime

import asyncpg

from app.backtest.service import run_study
from app.backtest.strategies import DAILY_LONG_BASELINES
from app.core.config import get_settings


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--capital-paisa", type=int, default=10_000_000)
    parser.add_argument("--quantity", type=int, default=10)
    args = parser.parse_args()
    cfg = get_settings()
    conn = await asyncpg.connect(cfg.database_url.get_secret_value())
    try:
        for spec in DAILY_LONG_BASELINES:
            run_id = await run_study(
                conn,
                identifier=spec.id,
                symbol=args.symbol,
                start_day=args.start,
                end_day=args.end,
                capital_paisa=args.capital_paisa,
                quantity=args.quantity,
                settings=cfg,
                now=datetime.now(UTC),
            )
            row = await conn.fetchrow(
                """SELECT r.id,r.strategy_id,r.status,r.detail,m.* FROM app.backtest_runs r
                   LEFT JOIN app.backtest_metrics m ON m.run_id=r.id WHERE r.id=$1""",
                run_id,
            )
            print(json.dumps(dict(row), default=str))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
