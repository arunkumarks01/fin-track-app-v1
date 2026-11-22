-- Phase 5: Paper sandbox + safety (auto exits, trailing stops, live marks, derivatives, Telegram)

-- Auto exits
ALTER TABLE app.paper_orders
    ADD COLUMN exit_order_id bigint REFERENCES app.paper_orders(id),
    ADD COLUMN exit_reason text CHECK (exit_reason IN ('stop_hit', 'target_hit', 'manual')),
    ADD COLUMN exit_triggered_at timestamptz;

CREATE INDEX paper_orders_exit_pending ON app.paper_orders (account_id, id)
    WHERE exit_order_id IS NULL AND status IN ('filled', 'partially_filled');

-- Trailing stops
ALTER TABLE app.paper_orders
    ADD COLUMN trailing_stop_bps smallint DEFAULT 0 CHECK (trailing_stop_bps >= 0),
    ADD COLUMN trailing_stop_paisa_min int DEFAULT 0 CHECK (trailing_stop_paisa_min >= 0),
    ADD COLUMN highest_paisa_seen int,
    ADD COLUMN current_stop_paisa int;

-- Live marks
ALTER TABLE app.paper_positions
    ADD COLUMN mark_updated_at timestamptz,
    ADD COLUMN mtm_paisa bigint DEFAULT 0;

CREATE TABLE app.paper_marks (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES app.paper_accounts(id),
    symbol text NOT NULL,
    mark_paisa int NOT NULL CHECK (mark_paisa > 0),
    marked_at timestamptz NOT NULL,
    UNIQUE (account_id, symbol, marked_at)
);

CREATE INDEX paper_marks_account ON app.paper_marks (account_id, marked_at DESC);

-- Derivatives
ALTER TABLE app.paper_orders
    ADD COLUMN contract_type text DEFAULT 'equity' CHECK (contract_type IN ('equity', 'fut', 'opt_ce', 'opt_pe')),
    ADD COLUMN expiry_date date,
    ADD COLUMN strike_paisa int,
    ADD COLUMN multiplier int DEFAULT 1 CHECK (multiplier > 0);

CREATE TABLE app.derivatives_specs (
    symbol text PRIMARY KEY,
    contract_type text NOT NULL CHECK (contract_type IN ('equity', 'fut', 'opt_ce', 'opt_pe')),
    expiry_date date,
    strike_paisa int,
    multiplier int NOT NULL CHECK (multiplier > 0),
    tick_size_paisa int NOT NULL CHECK (tick_size_paisa > 0),
    margin_initial int NOT NULL CHECK (margin_initial >= 0),
    margin_maint int NOT NULL CHECK (margin_maint >= 0),
    cached_at timestamptz NOT NULL DEFAULT now()
);

-- Telegram approvals
CREATE TABLE app.telegram_approvals (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES app.paper_accounts(id),
    order_id bigint NOT NULL UNIQUE REFERENCES app.paper_orders(id),
    message_id text NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'timed_out')),
    sent_at timestamptz NOT NULL,
    responded_at timestamptz,
    response_text text
);

CREATE INDEX telegram_approvals_pending ON app.telegram_approvals (sent_at)
    WHERE status = 'pending';
