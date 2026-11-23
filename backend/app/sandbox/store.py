"""Transactional replay ledger. No broker order or live execution dependency."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ, canonical_daily_ts
from app.data.extract_daily import last_final_daily_session
from app.risk.correlation import portfolio_correlation
from app.risk.parameters import PaperMode, RiskParameters
from app.risk.plans import BPS
from app.risk.sizing import Portfolio, Sizing, checks
from app.sandbox.model import Bar, Instruction, fees, match, sold_cost
from app.sandbox.parameters import PaperParameters


class PaperError(ValueError):
    """A paper action cannot proceed with the supplied data."""

    def __init__(self, detail: str, status: int = 409) -> None:
        super().__init__(detail)
        self.status = status


async def _lock_controls(conn: Any) -> None:
    control = await conn.fetchrow(
        "SELECT paused,killed FROM app.paper_controls WHERE id FOR UPDATE"
    )
    if control is None or control["paused"] or control["killed"]:
        raise PaperError("Paper trading is paused or the kill switch is active")


async def _account(conn: Any, account_id: int, *, lock: bool = False) -> Any:
    lookup = (
        "SELECT * FROM app.paper_accounts WHERE id = $1 FOR UPDATE"
        if lock
        else "SELECT * FROM app.paper_accounts WHERE id = $1"
    )
    account = await conn.fetchrow(lookup, account_id)
    if account is None:
        raise PaperError("Paper account not found", 404)
    return account


async def listing(conn: Any, now: datetime, settings: Settings) -> dict[str, Any]:
    final = last_final_daily_session(now, settings.daily_bar_final_next_day_ist)
    coverage = await conn.fetchrow(
        """SELECT min(trade_date) AS oldest, max(trade_date) AS newest
           FROM app.nse_archive_files WHERE status='ok' AND trade_date <= $1""",
        final,
    )
    controls = await conn.fetchrow("SELECT paused,killed FROM app.paper_controls WHERE id")
    accounts = await conn.fetch("SELECT * FROM app.paper_accounts ORDER BY id DESC")
    return {
        "coverage": dict(coverage),
        "controls": dict(controls),
        "accounts": [dict(row) for row in accounts],
        "default_mode": settings.paper_submode,
    }


async def create(
    conn: Any,
    name: str,
    start: date,
    mode: PaperMode,
    now: datetime,
    settings: Settings,
) -> dict[str, Any]:
    final = last_final_daily_session(now, settings.daily_bar_final_next_day_ist)
    earliest = await conn.fetchval(
        "SELECT min(trade_date) FROM app.nse_archive_files WHERE status = 'ok'"
    )
    if earliest is None or not earliest <= start <= final:
        raise PaperError("Start date is outside the finalized NSE archive", 422)
    has_date = await conn.fetchval(
        """SELECT EXISTS (SELECT 1 FROM app.nse_archive_files
                          WHERE status = 'ok' AND trade_date BETWEEN $1 AND $2)""",
        start,
        final,
    )
    if not has_date:
        raise PaperError("No archived session is available after the requested date", 422)
    row = await conn.fetchrow(
        """INSERT INTO app.paper_accounts
             (name,start_day,paper_mode,created_at,capital_paisa,cash_paisa,
              paper_parameters,risk_parameters)
           VALUES ($1,$2,$3,$4,$5,$5,$6::jsonb,$7::jsonb) RETURNING *""",
        name,
        start,
        mode,
        now,
        settings.risk.paper_capital_paisa,
        settings.paper.model_dump_json(),
        settings.risk.model_dump_json(),
    )
    return dict(row)


async def overview(conn: Any, account_id: int) -> dict[str, Any]:
    account = await _account(conn, account_id)
    positions = [
        dict(row)
        for row in await conn.fetch(
            "SELECT * FROM app.paper_positions WHERE account_id=$1 ORDER BY symbol",
            account_id,
        )
    ]
    orders = [
        dict(row)
        for row in await conn.fetch(
            """SELECT id,symbol,side,kind,status,quantity,filled,created_at,
                      triggered_on,detail,instruction
           FROM app.paper_orders WHERE account_id=$1 ORDER BY
             (status IN ('pending','partially_filled')) DESC,id DESC LIMIT 100""",
            account_id,
        )
    ]
    for row in orders:
        row["instruction"] = json.loads(row["instruction"])
    fills = [
        dict(row)
        for row in await conn.fetch(
            """SELECT f.*,o.symbol,o.side FROM app.paper_fills f
           JOIN app.paper_orders o ON o.id=f.order_id
           WHERE o.account_id=$1 ORDER BY f.id DESC LIMIT 100""",
            account_id,
        )
    ]
    for row in fills:
        row["fee_breakdown"] = json.loads(row["fee_breakdown"])
    market_value = sum(p["mark_paisa"] * p["quantity"] for p in positions)
    model = PaperParameters.model_validate_json(account["paper_parameters"])
    return {
        "account": dict(account),
        "positions": positions,
        "orders": orders,
        "fills": fills,
        "market_value_paisa": market_value,
        "equity_paisa": account["cash_paisa"] + market_value,
        "unrealized_paisa": market_value - sum(p["cost_paisa"] for p in positions),
        "model": model.model_dump(),
    }


async def submit(
    conn: Any,
    account_id: int,
    request_id: UUID,
    instruction: Instruction,
    now: datetime,
) -> dict[str, Any]:
    async with conn.transaction():
        await _lock_controls(conn)
        account = await _account(conn, account_id, lock=True)
        payload = instruction.model_dump()
        existing = await conn.fetchrow(
            "SELECT * FROM app.paper_orders WHERE account_id=$1 AND request_id=$2",
            account_id,
            request_id,
        )
        if existing:
            if json.loads(existing["instruction"]) != payload:
                raise PaperError("This request ID belongs to a different order")
            return dict(existing)
        equity = await conn.fetchval(
            "SELECT kind='equity' FROM app.universe WHERE symbol=$1",
            instruction.symbol,
        )
        if not equity:
            raise PaperError("This sandbox supports NSE equity symbols in the universe", 422)
        if instruction.side == "sell":
            held = (
                await conn.fetchval(
                    "SELECT quantity FROM app.paper_positions WHERE account_id=$1 AND symbol=$2",
                    account_id,
                    instruction.symbol,
                )
                or 0
            )
            reserved = await conn.fetchval(
                """SELECT coalesce(sum(quantity-filled),0) FROM app.paper_orders
                   WHERE account_id=$1 AND symbol=$2 AND side='sell'
                     AND status IN ('pending','partially_filled')""",
                account_id,
                instruction.symbol,
            )
            if instruction.quantity > held - reserved:
                raise PaperError("Sell quantity exceeds available paper shares")
        elif account["paper_mode"] == "realistic":
            if instruction.risk_stop_paisa is None or instruction.risk_target_paisa is None:
                raise PaperError("Realistic mode requires a stop and target for a buy", 422)
            duplicate = await conn.fetchval(
                """SELECT EXISTS (SELECT 1 FROM app.paper_positions
                                  WHERE account_id=$1 AND symbol=$2)
                      OR EXISTS (SELECT 1 FROM app.paper_orders WHERE account_id=$1 AND symbol=$2
                             AND side='buy' AND status IN ('pending','partially_filled'))""",
                account_id,
                instruction.symbol,
            )
            if duplicate:
                raise PaperError("Realistic mode allows one open position or buy order per symbol")
        row = await conn.fetchrow(
            """INSERT INTO app.paper_orders
                 (account_id,request_id,symbol,side,kind,status,quantity,instruction,created_at)
               VALUES ($1,$2,$3,$4,$5,'pending',$6,$7::jsonb,$8) RETURNING *""",
            account_id,
            request_id,
            instruction.symbol,
            instruction.side,
            instruction.kind,
            instruction.quantity,
            instruction.model_dump_json(),
            now,
        )
        return dict(row)


async def cancel(conn: Any, account_id: int, order_id: int) -> None:
    async with conn.transaction():
        await _account(conn, account_id, lock=True)
        result = await conn.fetchval(
            """UPDATE app.paper_orders SET status='cancelled',detail='Cancelled manually'
               WHERE account_id=$1 AND id=$2 AND status IN ('pending','partially_filled')
               RETURNING id""",
            account_id,
            order_id,
        )
        if result is None:
            raise PaperError("Order has no cancellable remainder")


def _realistic_rejection(
    instruction: Instruction,
    cfg: RiskParameters,
    holdings: dict[str, dict[str, Any]],
    bars: dict[str, Bar],
    cash: int,
    start_equity: int,
    day_trades: int,
    quantity: int,
    price: int,
    charges: int,
    correlation: int | None = None,
) -> str | None:
    stop, target = instruction.risk_stop_paisa, instruction.risk_target_paisa
    if stop is None or target is None or not stop < price < target:
        return "Fill price is outside the planned stop and target"
    position_cost = price * quantity + charges
    # Reserve the entire requested notional/risk for this instruction across partial fills.
    order_cost = price * instruction.quantity + charges
    risk = (price - stop) * instruction.quantity + charges
    opening_equity = cash + sum(
        pos["quantity"] * (bars[symbol].open if symbol in bars else pos["mark_paisa"])
        for symbol, pos in holdings.items()
    )
    already_open = instruction.symbol in holdings
    portfolio = Portfolio(
        cash,
        opening_equity - start_equity,
        len(holdings) - already_open,
        day_trades,
        correlation_bps=correlation,
    )
    sizing = Sizing(
        "fixed_lots",
        quantity,
        quantity,
        risk,
        order_cost,
        cfg.paper_capital_paisa * cfg.risk_per_trade_bps // BPS,
        cfg.paper_capital_paisa * cfg.max_notional_bps // BPS,
    )
    result = checks(
        (target - price) * BPS // (price - stop),
        sizing,
        portfolio,
        cfg,
        "realistic",
        "underlying",
    )
    # The full instruction is checked for risk, while only this fill reduces cash.
    if position_cost > cash:
        return "Insufficient paper cash"
    if (
        cfg.correlation_limit_bps is not None
        and correlation is not None
        and correlation >= cfg.correlation_limit_bps
        and risk > sizing.risk_budget_paisa * cfg.correlation_size_bps // BPS
    ):
        return "Correlated positions exceed the reduced risk budget"
    return "; ".join(result["warnings"] + result["unknown"]) if result["blocks"] else None


async def advance(
    conn: Any,
    account_id: int,
    expected: date | None,
    now: datetime,
    settings: Settings,
) -> dict[str, Any]:
    async with conn.transaction():
        await _lock_controls(conn)
        account = await _account(conn, account_id, lock=True)
        if expected != account["cursor_day"]:
            raise PaperError("Replay already advanced. Refresh the account before trying again")
        final = last_final_daily_session(now, settings.daily_bar_final_next_day_ist)
        start = account["start_day"] if expected is None else expected + timedelta(days=1)
        day: date | None = await conn.fetchval(
            """SELECT min(day) FROM (
                 SELECT trade_date AS day FROM app.nse_archive_files
                 WHERE trade_date BETWEEN $1 AND $2
                 UNION ALL
                 SELECT (ts AT TIME ZONE 'Asia/Kolkata')::date AS day
                 FROM app.ohlcv_candles WHERE symbol = $3 AND timeframe = '1d'
                   AND ts >= $4 AND ts < $5
               ) AS sessions""",
            start,
            final,
            settings.intraday_benchmarks.get("NSE"),
            canonical_daily_ts(start, MARKET_TZ),
            canonical_daily_ts(final + timedelta(days=1), MARKET_TZ),
        )
        if not day:
            raise PaperError("No later finalized NSE archive session is available")
        archive_status = await conn.fetchval(
            "SELECT status FROM app.nse_archive_files WHERE trade_date=$1",
            day,
        )
        if archive_status != "ok":
            raise PaperError(f"NSE session {day.isoformat()} is missing or unverified")
        orders = await conn.fetch(
            """SELECT * FROM app.paper_orders WHERE account_id=$1
               AND status IN ('pending','partially_filled') ORDER BY id""",
            account_id,
        )
        holdings = {
            p["symbol"]: dict(p)
            for p in await conn.fetch(
                "SELECT * FROM app.paper_positions WHERE account_id=$1",
                account_id,
            )
        }
        symbols = sorted(set(holdings) | {o["symbol"] for o in orders})
        action = await conn.fetchval(
            """SELECT symbol FROM app.corporate_actions
               WHERE symbol=ANY($1::text[]) AND ex_date=$2
               ORDER BY symbol LIMIT 1""",
            symbols,
            day,
        )
        if action:
            raise PaperError(
                f"Replay stopped at a corporate action for {action} on {day.isoformat()}"
            )
        bars = {
            b["symbol"]: Bar(b["open"], b["high"], b["low"], b["close"], b["volume"])
            for b in await conn.fetch(
                """SELECT symbol,open,high,low,close,volume FROM app.nse_bhav
                   WHERE trade_date=$1 AND symbol=ANY($2::text[])""",
                day,
                symbols,
            )
        }
        # Advancing without a mark for a held symbol would leave yesterday's price
        # displayed as current and feed stale values into portfolio guardrails.
        missing_marks = sorted(set(holdings) - set(bars))
        if missing_marks:
            raise PaperError(
                f"Replay stopped on {day.isoformat()}: missing equity marks for "
                + ", ".join(missing_marks)
            )
        model = PaperParameters.model_validate_json(account["paper_parameters"])
        risk_settings = RiskParameters.model_validate_json(account["risk_parameters"])
        remaining = {symbol: bar.volume * model.volume_bps // BPS for symbol, bar in bars.items()}
        cash, realized = account["cash_paisa"], account["realized_paisa"]
        start_equity = cash + sum(p["quantity"] * p["mark_paisa"] for p in holdings.values())
        fills = day_trades = 0
        for order in orders:
            instruction = Instruction.model_validate_json(order["instruction"])
            symbol = instruction.symbol
            bar = bars.get(symbol)
            if bar is None:
                # Preserve GTT instructions through a verified no-trade session.
                if instruction.kind != "gtt":
                    await _update_order(
                        conn, order["id"], "cancelled", order["filled"], "No equity bar"
                    )
                continue
            result = match(instruction, bar, order["triggered_on"] is not None, model)
            if result.arm:
                await conn.execute(
                    "UPDATE app.paper_orders SET triggered_on=$1,detail=$2 WHERE id=$3",
                    day,
                    "GTT triggered. Limit order activates next session",
                    order["id"],
                )
                continue
            position = holdings.get(symbol)
            held = position["quantity"] if position else 0
            quantity = min(order["quantity"] - order["filled"], remaining[symbol])
            if instruction.side == "sell":
                quantity = min(quantity, held)
            price = result.price
            if price is None or quantity <= 0:
                if instruction.kind != "gtt":
                    await _update_order(
                        conn,
                        order["id"],
                        "cancelled",
                        order["filled"],
                        "Price or available session volume did not permit a fill",
                    )
                continue
            notional = price * quantity
            breakdown = fees(notional, instruction.side, model)
            total = sum(breakdown.values())
            if instruction.side == "buy" and account["paper_mode"] == "realistic":
                correlation = None
                if risk_settings.correlation_limit_bps is not None:
                    correlation = await portfolio_correlation(
                        conn,
                        symbol,
                        list(holdings),
                        canonical_daily_ts(day, MARKET_TZ),
                        risk_settings.correlation_window_sessions,
                        risk_settings.correlation_min_observations,
                    )
                reason = _realistic_rejection(
                    instruction,
                    risk_settings,
                    holdings,
                    bars,
                    cash,
                    start_equity,
                    day_trades,
                    quantity,
                    price,
                    total,
                    correlation,
                )
                if reason:
                    await _update_order(conn, order["id"], "rejected", order["filled"], reason)
                    continue
            cost = position["cost_paisa"] if position else 0
            outcome = None
            if instruction.side == "buy":
                cash -= notional + total
                units, cost = held + quantity, cost + notional + total
                day_trades += 1
            else:
                allocated = sold_cost(cost, held, quantity)
                cash += notional - total
                outcome = notional - total - allocated
                realized += outcome
                units, cost = held - quantity, cost - allocated
            if units:
                holdings[symbol] = {"quantity": units, "cost_paisa": cost, "mark_paisa": bar.open}
            else:
                holdings.pop(symbol)
            await conn.execute(
                """INSERT INTO app.paper_fills
                       (order_id,trade_day,quantity,price_paisa,fees_paisa,fee_breakdown,realized_paisa)
                   VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7)""",
                order["id"],
                day,
                quantity,
                price,
                total,
                json.dumps(breakdown),
                outcome,
            )
            remaining[symbol] -= quantity
            filled = order["filled"] + quantity
            status = (
                "filled"
                if filled == order["quantity"]
                else "partially_filled"
                if instruction.kind == "gtt"
                else "cancelled"
            )
            detail = (
                None
                if status == "filled"
                else "Partial fill. Remaining shares are pending"
                if status == "partially_filled"
                else "Partial fill. Day-order remainder expired"
            )
            await _update_order(conn, order["id"], status, filled, detail)
            fills += 1

        await conn.execute(
            "DELETE FROM app.paper_positions WHERE account_id=$1 AND NOT (symbol=ANY($2::text[]))",
            account_id,
            list(holdings),
        )
        for symbol, pos in holdings.items():
            if symbol not in bars:
                continue
            await conn.execute(
                """INSERT INTO app.paper_positions
                   (account_id,symbol,quantity,cost_paisa,mark_paisa,mark_day)
                   VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (account_id,symbol)
                   DO UPDATE SET quantity=excluded.quantity,cost_paisa=excluded.cost_paisa,
                         mark_paisa=excluded.mark_paisa,mark_day=excluded.mark_day""",
                account_id,
                symbol,
                pos["quantity"],
                pos["cost_paisa"],
                bars[symbol].close,
                day,
            )
        await conn.execute(
            """UPDATE app.paper_accounts SET cursor_day=$1,cash_paisa=$2,realized_paisa=$3
               WHERE id=$4""",
            day,
            cash,
            realized,
            account_id,
        )
        return {"trade_day": day.isoformat(), "fills": fills}


async def _update_order(
    conn: Any,
    order_id: int,
    status: str,
    filled: int,
    detail: str | None,
) -> None:
    await conn.execute(
        "UPDATE app.paper_orders SET status=$1,filled=$2,detail=$3 WHERE id=$4",
        status,
        filled,
        detail,
        order_id,
    )
