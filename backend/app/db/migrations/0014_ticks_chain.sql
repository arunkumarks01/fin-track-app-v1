-- Slice 1d (DEC-1D-TICKS / DEC-1D-GREEKS, locked by the user 2026-11-21): live tick capture,
-- fail-closed Parquet export, and raw option-chain snapshots.

-- The Phase-0 placeholder had no rows and a wide schema; replace it with the compact one.
DO $$
BEGIN
    IF (SELECT count(*) FROM app.ticks) > 0 THEN
        RAISE EXCEPTION 'app.ticks is not empty: refusing to replace it';
    END IF;
END $$;
DROP TABLE app.ticks;

-- One row per Kite tick (Full mode for tradables, index packets for indices). Compact like
-- ohlcv_1m: smallint local id, int4 paisa, 5-level depth as fixed columns (not arrays).
-- ts = when WE received it; ltt/ext = Kite's last-trade and exchange times (seconds). A value
-- that does not fit its column is rejected by the writer, never clipped.
CREATE TABLE app.ticks (
    ts      timestamptz NOT NULL,
    sid     smallint    NOT NULL,
    ltp     integer     NOT NULL CHECK (ltp > 0),
    ltq     integer     CHECK (ltq >= 0),
    atp     integer     CHECK (atp >= 0),
    volume  bigint      CHECK (volume >= 0),
    buy_qty bigint      CHECK (buy_qty >= 0),
    sell_qty bigint     CHECK (sell_qty >= 0),
    oi      bigint      CHECK (oi >= 0),
    ltt     timestamptz,
    ext     timestamptz,
    b1p int4 CHECK (b1p >= 0), b1q int4 CHECK (b1q >= 0), b1o smallint CHECK (b1o >= 0),
    b2p int4 CHECK (b2p >= 0), b2q int4 CHECK (b2q >= 0), b2o smallint CHECK (b2o >= 0),
    b3p int4 CHECK (b3p >= 0), b3q int4 CHECK (b3q >= 0), b3o smallint CHECK (b3o >= 0),
    b4p int4 CHECK (b4p >= 0), b4q int4 CHECK (b4q >= 0), b4o smallint CHECK (b4o >= 0),
    b5p int4 CHECK (b5p >= 0), b5q int4 CHECK (b5q >= 0), b5o smallint CHECK (b5o >= 0),
    a1p int4 CHECK (a1p >= 0), a1q int4 CHECK (a1q >= 0), a1o smallint CHECK (a1o >= 0),
    a2p int4 CHECK (a2p >= 0), a2q int4 CHECK (a2q >= 0), a2o smallint CHECK (a2o >= 0),
    a3p int4 CHECK (a3p >= 0), a3q int4 CHECK (a3q >= 0), a3o smallint CHECK (a3o >= 0),
    a4p int4 CHECK (a4p >= 0), a4q int4 CHECK (a4q >= 0), a4o smallint CHECK (a4o >= 0),
    a5p int4 CHECK (a5p >= 0), a5q int4 CHECK (a5q >= 0), a5o smallint CHECK (a5o >= 0),
    CONSTRAINT ticks_sid_positive CHECK (sid > 0)
);
SELECT create_hypertable('app.ticks', by_range('ts', INTERVAL '24 hours'));
CREATE INDEX ticks_sid_ts_idx ON app.ticks (sid, ts DESC);

-- One row per capture session: what ran, how much, and why it stopped.
CREATE TABLE app.tick_capture_runs (
    id            bigserial   PRIMARY KEY,
    session_date  date        NOT NULL,
    started_at    timestamptz NOT NULL,
    ended_at      timestamptz,
    status        text        NOT NULL CHECK (status IN ('running', 'ok', 'failed', 'no_session')),
    instruments   integer     NOT NULL DEFAULT 0,
    ticks         bigint      NOT NULL DEFAULT 0,
    rejected      bigint      NOT NULL DEFAULT 0,
    reconnects    integer     NOT NULL DEFAULT 0,
    detail        text
);

-- The Parquet export ledger. A chunk is dropped from the database ONLY after its file was
-- written, read back and matched the database (row count + checksums): status 'verified'.
CREATE TABLE app.tick_exports (
    chunk_start timestamptz PRIMARY KEY,
    chunk_end   timestamptz NOT NULL,
    status      text        NOT NULL CHECK (status IN ('verified', 'mismatch', 'error')),
    rows        bigint      NOT NULL DEFAULT 0,
    bytes       bigint      NOT NULL DEFAULT 0,
    sha256      text,
    path        text,
    checksums   jsonb,
    detail      text,
    exported_at timestamptz NOT NULL,
    dropped_at  timestamptz
);

-- Raw option-chain snapshots every 5 minutes (narrow scope, DEC-1D-CHAIN). Greeks and IV are
-- computed in Phase 2 from these; they are never the only record.
CREATE TABLE app.option_snapshot_meta (
    snapshot_ts timestamptz NOT NULL,
    underlying  text        NOT NULL,
    spot_paisa  integer     CHECK (spot_paisa > 0),
    fut_paisa   integer     CHECK (fut_paisa > 0),
    fut_expiry  date,
    contracts   integer     NOT NULL DEFAULT 0,
    status      text        NOT NULL CHECK (status IN ('ok', 'partial', 'error')),
    detail      text,
    PRIMARY KEY (snapshot_ts, underlying)
);
CREATE TABLE app.option_snapshots (
    snapshot_ts      timestamptz NOT NULL,
    underlying       text        NOT NULL,
    instrument_token bigint      NOT NULL,
    expiry           date        NOT NULL,
    strike_paisa     integer     NOT NULL CHECK (strike_paisa > 0),
    opt_type         text        NOT NULL CHECK (opt_type IN ('CE', 'PE')),
    ltp              integer     CHECK (ltp >= 0),
    volume           bigint      CHECK (volume >= 0),
    oi               bigint      CHECK (oi >= 0),
    bid              integer     CHECK (bid >= 0),
    bid_qty          bigint      CHECK (bid_qty >= 0),
    ask              integer     CHECK (ask >= 0),
    ask_qty          bigint      CHECK (ask_qty >= 0),
    ltt              timestamptz
);
SELECT create_hypertable('app.option_snapshots', by_range('snapshot_ts', INTERVAL '7 days'));
CREATE UNIQUE INDEX option_snapshots_key
    ON app.option_snapshots (snapshot_ts, underlying, instrument_token);
CREATE INDEX option_snapshots_underlying_idx ON app.option_snapshots (underlying, snapshot_ts DESC);
