-- Phase 6: immutable historical-study ledger. Run inputs and generated results stay together.
CREATE TABLE app.backtest_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    strategy_id text NOT NULL,
    strategy_revision integer NOT NULL CHECK (strategy_revision > 0),
    symbol text NOT NULL REFERENCES app.universe(symbol),
    start_day date NOT NULL,
    end_day date NOT NULL CHECK (end_day >= start_day),
    capital_paisa bigint NOT NULL CHECK (capital_paisa > 0),
    assumptions jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    detail text,
    created_at timestamptz NOT NULL,
    completed_at timestamptz,
    CHECK ((status = 'completed') = (completed_at IS NOT NULL))
);

CREATE TABLE app.backtest_fills (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES app.backtest_runs(id) ON DELETE CASCADE,
    trade_day date NOT NULL,
    side text NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity integer NOT NULL CHECK (quantity > 0),
    price_paisa integer NOT NULL CHECK (price_paisa > 0),
    fees_paisa integer NOT NULL CHECK (fees_paisa >= 0),
    realized_paisa bigint,
    UNIQUE (run_id, trade_day, side, id)
);

CREATE TABLE app.backtest_equity (
    run_id bigint NOT NULL REFERENCES app.backtest_runs(id) ON DELETE CASCADE,
    trade_day date NOT NULL,
    equity_paisa bigint NOT NULL,
    PRIMARY KEY (run_id, trade_day)
);

CREATE TABLE app.backtest_metrics (
    run_id bigint PRIMARY KEY REFERENCES app.backtest_runs(id) ON DELETE CASCADE,
    ending_equity_paisa bigint NOT NULL,
    net_paisa bigint NOT NULL,
    max_drawdown_numerator bigint NOT NULL CHECK (max_drawdown_numerator >= 0),
    max_drawdown_denominator bigint NOT NULL CHECK (max_drawdown_denominator > 0),
    closed_trades integer NOT NULL CHECK (closed_trades >= 0),
    wins integer NOT NULL CHECK (wins >= 0),
    losses integer NOT NULL CHECK (losses >= 0),
    gross_profit_paisa bigint NOT NULL CHECK (gross_profit_paisa >= 0),
    gross_loss_paisa bigint NOT NULL CHECK (gross_loss_paisa >= 0)
);

CREATE INDEX backtest_runs_strategy_created ON app.backtest_runs(strategy_id, created_at DESC);
