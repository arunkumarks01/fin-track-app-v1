CREATE TABLE app.paper_controls (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    paused boolean NOT NULL DEFAULT false,
    killed boolean NOT NULL DEFAULT false
);
INSERT INTO app.paper_controls DEFAULT VALUES;

CREATE TABLE app.paper_accounts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    start_day date NOT NULL,
    cursor_day date CHECK (cursor_day >= start_day),
    created_at timestamptz NOT NULL,
    paper_mode text NOT NULL CHECK (paper_mode IN ('learning', 'realistic')),
    capital_paisa bigint NOT NULL CHECK (capital_paisa > 0),
    cash_paisa bigint NOT NULL,
    paper_parameters jsonb NOT NULL,
    risk_parameters jsonb NOT NULL,
    realized_paisa bigint NOT NULL DEFAULT 0
);

CREATE TABLE app.paper_orders (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES app.paper_accounts(id),
    request_id uuid NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL CHECK (side IN ('buy','sell')),
    kind text NOT NULL CHECK (kind IN ('market','limit','stop_market','gtt')),
    status text NOT NULL CHECK (status IN ('pending','filled','partially_filled','cancelled','rejected')),
    quantity bigint NOT NULL CHECK (quantity > 0),
    filled bigint NOT NULL DEFAULT 0 CHECK (filled BETWEEN 0 AND quantity),
    instruction jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    triggered_on date,
    detail text,
    UNIQUE (account_id, request_id)
);
CREATE INDEX paper_orders_open ON app.paper_orders (account_id,id)
WHERE status IN ('pending','partially_filled');

CREATE TABLE app.paper_positions (
    account_id bigint NOT NULL REFERENCES app.paper_accounts(id),
    symbol text NOT NULL,
    quantity bigint NOT NULL CHECK (quantity > 0),
    cost_paisa bigint NOT NULL CHECK (cost_paisa >= 0),
    mark_paisa bigint NOT NULL CHECK (mark_paisa > 0),
    mark_day date NOT NULL,
    PRIMARY KEY (account_id, symbol)
);

CREATE TABLE app.paper_fills (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES app.paper_orders(id),
    trade_day date NOT NULL,
    quantity bigint NOT NULL CHECK (quantity > 0),
    price_paisa bigint NOT NULL CHECK (price_paisa > 0),
    fees_paisa bigint NOT NULL CHECK (fees_paisa >= 0),
    fee_breakdown jsonb NOT NULL,
    realized_paisa bigint,
    UNIQUE (order_id, trade_day)
);
