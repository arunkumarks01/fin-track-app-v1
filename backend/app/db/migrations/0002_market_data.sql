-- 0002_market_data — instrument master, F&O universe, Kite session, ingestion state,
-- quarantine and data-quality flags (Phase 1a). Plain tables: all small.
-- Prices are integer paisa. Never edit an applied migration — add a new one.

-- Kite instrument master: a full daily snapshot, replaced atomically on refresh.
CREATE TABLE app.instruments (
    instrument_token  bigint      PRIMARY KEY,
    exchange_token    bigint      NOT NULL,
    tradingsymbol     text        NOT NULL,
    name              text,
    exchange          text        NOT NULL,
    segment           text        NOT NULL,
    instrument_type   text        NOT NULL,
    expiry            date,
    strike            bigint      CHECK (strike IS NULL OR strike >= 0),
    tick_size         bigint      NOT NULL CHECK (tick_size >= 0),
    lot_size          integer     NOT NULL CHECK (lot_size >= 0),
    refreshed_at      timestamptz NOT NULL,
    CONSTRAINT instruments_exchange_symbol UNIQUE (exchange, tradingsymbol)
);

-- Symbols we ingest. `symbol` is 'EXCHANGE:TRADINGSYMBOL' (e.g. 'NSE:RELIANCE', 'BSE:SENSEX'),
-- the same key used in app.ohlcv_candles. Members that leave the F&O list are deactivated,
-- never deleted (their history stays).
CREATE TABLE app.universe (
    symbol            text        PRIMARY KEY,
    instrument_token  bigint      NOT NULL,
    kind              text        NOT NULL CHECK (kind IN ('equity', 'index')),
    active            boolean     NOT NULL DEFAULT true,
    added_at          timestamptz NOT NULL DEFAULT now(),
    refreshed_at      timestamptz NOT NULL
);

-- Single-row Kite session (restart recovery; the hot copy lives in process memory).
CREATE TABLE app.kite_session (
    id            smallint    PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    user_id       text        NOT NULL,
    access_token  text        NOT NULL,
    login_time    timestamptz NOT NULL,
    expires_at    timestamptz NOT NULL,
    stored_at     timestamptz NOT NULL DEFAULT now()
);

-- Resumable extraction: where each (symbol, timeframe) got to.
CREATE TABLE app.ingest_cursor (
    symbol        text        NOT NULL,
    timeframe     text        NOT NULL,
    last_ts       timestamptz,
    status        text        NOT NULL CHECK (status IN ('ok', 'error')),
    last_error    text,
    rows_written  bigint      NOT NULL DEFAULT 0 CHECK (rows_written >= 0),
    updated_at    timestamptz NOT NULL,
    PRIMARY KEY (symbol, timeframe)
);

-- Rows that failed the validation gate: kept with the raw payload and a reason, never
-- silently stored or dropped (ARCHITECTURE §5).
CREATE TABLE app.ohlcv_quarantine (
    id              bigserial   PRIMARY KEY,
    symbol          text        NOT NULL,
    timeframe       text        NOT NULL,
    ts              timestamptz,
    reason          text        NOT NULL CHECK (reason IN (
                        'unparseable', 'non_positive_price', 'ohlc_order',
                        'negative_volume', 'non_canonical_ts', 'duplicate_in_batch',
                        'conflicts_with_stored')),
    detail          text,
    raw             jsonb       NOT NULL,
    source          text        NOT NULL,
    quarantined_at  timestamptz NOT NULL
);
CREATE INDEX ohlcv_quarantine_symbol_idx ON app.ohlcv_quarantine (symbol, timeframe, ts);

-- Flag-but-keep findings on stored bars (e.g. suspected corporate action).
CREATE TABLE app.ohlcv_flags (
    symbol      text        NOT NULL,
    timeframe   text        NOT NULL,
    ts          timestamptz NOT NULL,
    flag        text        NOT NULL CHECK (flag IN ('large_gap', 'missing_sessions')),
    detail      text,
    flagged_at  timestamptz NOT NULL,
    PRIMARY KEY (symbol, timeframe, ts, flag)
);
