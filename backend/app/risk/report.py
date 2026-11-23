"""Read-only bridge between scored candidates and paper trade plans."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from app.analytics.indicators import Bar, atr
from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ
from app.risk.parameters import PaperMode, RiskParameters
from app.risk.plans import PriceContext, Side, build_levels
from app.risk.sizing import Portfolio, checks, size
from app.scoring.rules import Instrument, Mode, Value

_STRUCTURE = (
    "prior_252_high_paisa",
    "prior_252_low_paisa",
    "prior_20_high_paisa",
    "prior_20_low_paisa",
    "opening_range_high_paisa",
    "opening_range_low_paisa",
    "cpr_top_paisa",
    "cpr_bottom_paisa",
    "vwap_paisa",
    "previous_close_paisa",
)


def unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


async def next_session(conn: Any, symbol: str, day: date, settings: Settings) -> date | None:
    exchange = symbol.split(":", 1)[0]
    result: date | None = await conn.fetchval(
        """SELECT min(day) FROM (
             SELECT session_date AS day FROM app.exchange_sessions
             WHERE exchange = $1 AND session_date > $2
             UNION ALL
             SELECT (ts AT TIME ZONE 'Asia/Kolkata')::date
             FROM app.ohlcv_candles WHERE symbol = $3 AND timeframe = '1d'
               AND ts > $4
           ) AS future_sessions""",
        exchange,
        day,
        settings.intraday_benchmarks.get(exchange),
        datetime.combine(day, time.max, MARKET_TZ),
    )
    return result


async def account_portfolio(
    conn: Any, account_id: int, day: date
) -> tuple[Portfolio, RiskParameters, PaperMode]:
    """Load the persisted account inputs used to assess a candidate plan."""
    account = await conn.fetchrow(
        """SELECT cash_paisa, risk_parameters, paper_mode
           FROM app.paper_accounts WHERE id=$1""",
        account_id,
    )
    if account is None:
        raise ValueError("Paper account not found")
    positions = await conn.fetchval(
        "SELECT count(*) FROM app.paper_positions WHERE account_id=$1", account_id
    )
    trades = await conn.fetchval(
        """SELECT count(DISTINCT f.order_id) FROM app.paper_fills f
           JOIN app.paper_orders o ON o.id=f.order_id
           WHERE o.account_id=$1 AND f.trade_day=$2""",
        account_id,
        day,
    )
    realized = await conn.fetchval(
        """SELECT coalesce(sum(f.realized_paisa), 0) FROM app.paper_fills f
           JOIN app.paper_orders o ON o.id=f.order_id
           WHERE o.account_id=$1 AND f.trade_day=$2""",
        account_id,
        day,
    )
    return (
        Portfolio(
            int(account["cash_paisa"]),
            int(realized),
            int(positions),
            int(trades),
        ),
        RiskParameters.model_validate_json(account["risk_parameters"]),
        account["paper_mode"],
    )


async def _derivative_context(
    conn: Any,
    contract: dict[str, Any],
    underlying: str,
    day: date,
    settings: Settings,
) -> tuple[PriceContext, int] | None:
    expiry = date.fromisoformat(contract["expiry"])
    opt_type = contract["type"]
    strike = contract.get("strike_paisa", 0)
    spec = await conn.fetchrow(
        """SELECT tick_size, lot_size FROM app.instruments
           WHERE exchange = 'NFO' AND name = $1 AND expiry = $2 AND instrument_type = $3
             AND coalesce(strike, 0) = $4 AND tick_size > 0 AND lot_size > 0
           ORDER BY instrument_token LIMIT 1""",
        underlying,
        expiry,
        opt_type,
        strike,
    )
    if spec is None:
        return None
    period = settings.analytics_periods.atr
    # Require a contiguous series on known benchmark sessions for this exact contract.
    rows = await conn.fetch(
        """WITH sessions AS (
               SELECT (ts AT TIME ZONE 'Asia/Kolkata')::date AS day
               FROM app.ohlcv_candles WHERE symbol = $1 AND timeframe = '1d'
                 AND ts <= $2 ORDER BY ts DESC LIMIT $3
           )
           SELECT f.trade_date, f.open_paisa, f.high_paisa, f.low_paisa, f.close_paisa, f.contracts
           FROM sessions s
           JOIN app.fo_daily f ON f.trade_date = s.day
           WHERE f.underlying = $4 AND f.expiry = $5 AND f.opt_type = $6
             AND f.strike_paisa = $7 AND f.contracts > 0
             AND f.low_paisa > 0 AND f.open_paisa BETWEEN f.low_paisa AND f.high_paisa
             AND f.close_paisa BETWEEN f.low_paisa AND f.high_paisa
           ORDER BY f.trade_date ASC""",
        settings.intraday_benchmarks.get("NSE"),
        datetime.combine(day, time.max, MARKET_TZ),
        period + 1,
        underlying,
        expiry,
        opt_type,
        strike,
    )
    if len(rows) != period + 1 or rows[-1]["trade_date"] != day:
        return None
    bars = [
        Bar(
            int(datetime.combine(r["trade_date"], time.min, MARKET_TZ).timestamp()),
            r["open_paisa"],
            r["high_paisa"],
            r["low_paisa"],
            r["close_paisa"],
            r["contracts"],
        )
        for r in rows
    ]
    volatility = atr(bars, period)[-1]
    if volatility is None or volatility <= 0:
        return None
    reference = bars[-1]
    if opt_type != "FUT":
        # Accept only the quoted ask of the vetted contract. Its prices must be on the same
        # session basis as the premium history and no intraday ATR is inferred from daily bars.
        ask = contract.get("ask_paisa")
        quote_at = contract.get("snapshot_at")
        if (
            not isinstance(ask, int)
            or not quote_at
            or datetime.fromisoformat(quote_at).astimezone(MARKET_TZ).date() != day
            or not reference.low <= ask <= reference.high
        ):
            return None
        reference = Bar(
            reference.t, reference.open, reference.high, reference.low, ask, reference.volume
        )
    structure = (max(b.high for b in bars[:-1]), min(b.low for b in bars[:-1]))
    return (
        PriceContext(
            reference.close,
            reference.high,
            reference.low,
            round(volatility),
            spec["tick_size"],
            structure,
        ),
        spec["lot_size"],
    )


async def attach_plans(
    conn: Any,
    items: list[dict[str, Any]],
    current: dict[str, Value],
    symbol: str,
    mode: Mode,
    instrument: Instrument,
    day: date,
    as_of: datetime,
    settings: Settings,
    account_id: int | None = None,
) -> None:
    matched = [item for item in items if item["status"] == "matched"]
    if not matched:
        return
    spec = None
    if instrument == "underlying":
        spec = await conn.fetchrow(
            """SELECT u.kind, i.tick_size, i.lot_size
               FROM app.universe u LEFT JOIN app.instruments i USING (instrument_token)
               WHERE u.symbol = $1""",
            symbol,
        )
    next_day = day if mode == "intraday" else await next_session(conn, symbol, day, settings)
    cutoff = settings.risk.entry_cutoff_ist
    portfolio: Portfolio | None = None
    risk = settings.risk
    paper_mode = settings.paper_submode
    if account_id is not None:
        portfolio, risk, paper_mode = await account_portfolio(conn, account_id, day)
        cutoff = risk.entry_cutoff_ist
    expiration = datetime.combine(next_day, cutoff, MARKET_TZ) if next_day else None
    cutoff_label = cutoff.isoformat(timespec="minutes")
    for item in matched:
        if instrument == "underlying":
            if spec is None or spec["kind"] != "equity":
                item["trade_plan"] = unavailable("An index signal needs a tradeable contract")
                continue
            if mode == "swing" and item["direction"] == "short":
                item["trade_plan"] = unavailable("Swing equity shorts need verified borrow data")
                continue
            if not spec["tick_size"] or not spec["lot_size"]:
                item["trade_plan"] = unavailable("Verified tick size or lot size is missing")
                continue
            values = [
                current.get(key) for key in ("close_paisa", "high_paisa", "low_paisa", "atr_paisa")
            ]
            if any(type(v) is not int for v in values):
                item["trade_plan"] = unavailable("A finalized price bar and ATR are required")
                continue
            close, high, low, volatility = (int(v) for v in values if v is not None)
            structure = tuple(v for k in _STRUCTURE if type(v := current.get(k)) is int)
            price = PriceContext(close, high, low, volatility, spec["tick_size"], structure)
            lot = spec["lot_size"]
        else:
            ticker = symbol.partition(":")[2]
            underlying = "NIFTY" if ticker == "NIFTY 50" else ticker
            prepared = (
                await _derivative_context(conn, item["contract"], underlying, day, settings)
                if item.get("contract") and mode == "swing"
                else None
            )
            if prepared is None:
                item["trade_plan"] = unavailable(
                    "The exact contract needs verified prices, ATR and lot size"
                )
                continue
            price, lot = prepared
        side: Side = "long" if instrument == "option" or item["direction"] == "long" else "short"
        levels = build_levels(price, side, risk)
        if levels is None:
            item["trade_plan"] = unavailable(
                "The stored price levels cannot form a valid risk plan"
            )
            continue
        sizing = size(levels, lot, instrument, risk, settings.risk_sizing_mode, portfolio)
        guardrails = checks(levels.reward_risk_bps, sizing, portfolio, risk, paper_mode, instrument)
        if expiration is None:
            guardrails["unknown"].append("The next trading session is not yet confirmed")
            if paper_mode == "realistic":
                guardrails["blocks"] = True
        expired = expiration is not None and as_of >= expiration
        if expired:
            guardrails["warnings"].append("The entry window has expired")
            guardrails["blocks"] = paper_mode == "realistic"
        # Positions are not opened here; the timestamp is an entry window, rather than an order.
        entry_session = "Signal session" if mode == "intraday" else "Next trading session"
        window = f"{entry_session} · {cutoff_label} IST"
        if (
            next_day
            and item.get("contract")
            and date.fromisoformat(item["contract"]["expiry"]) < next_day
        ):
            item["trade_plan"] = unavailable("The contract expires before the entry session")
            continue
        item["trade_plan"] = {
            "status": "expired" if expired else "draft",
            "levels": levels.to_dict(),
            "lot_size": lot,
            "tick_size_paisa": price.tick,
            "sizing": sizing.to_dict(),
            "capital_paisa": risk.paper_capital_paisa,
            "paper_account_id": account_id,
            "guardrails": guardrails,
            "entry_window": window,
            "expires_at": expiration.isoformat() if expiration else None,
            "price_basis": "adjusted underlying history"
            if instrument == "underlying"
            else "exact contract",
        }
