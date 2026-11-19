-- 0001_init — core time-series hypertables.
--
-- Apache-licensed TimescaleDB only (D25): the VPS runs the Apache build, so no
-- compression/columnstore, retention policies or continuous aggregates. Their fallbacks:
-- retention = in-process drop_chunks job (app/db/retention.py); higher timeframes =
-- scheduled rollup job (Phase 1); disk = Parquet cold tier (Phase 1).
-- Chunk intervals are applied at boot from config (app/db/policies.py).
-- All prices are integer paisa (bigint). Never edit an applied migration — add a new one.

-- ---------------------------------------------------------------------------
-- OHLCV candles. Dedup key = trading identity (ARCHITECTURE §5):
--   (symbol, timeframe, ts), where a 1d bar's ts is 00:00 IST of its trading date.
--   The CHECK makes a non-canonical daily ts impossible, so one trading day can
--   never land twice under different timestamps. 'Asia/Kolkata' is the NSE
--   exchange timezone (app.core.tradingdate.MARKET_TZ) — a fact, not a tunable.
-- Validity CHECKs are defence in depth; the Phase-1 validation gate quarantines
-- bad rows before they ever reach this table.
-- ---------------------------------------------------------------------------
CREATE TABLE app.ohlcv_candles (
    symbol       text        NOT NULL,
    timeframe    text        NOT NULL,
    ts           timestamptz NOT NULL,
    open         bigint      NOT NULL,
    high         bigint      NOT NULL,
    low          bigint      NOT NULL,
    close        bigint      NOT NULL,
    volume       bigint      NOT NULL,
    oi           bigint,
    ingested_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ohlcv_candles_dedup UNIQUE (symbol, timeframe, ts),
    CONSTRAINT ohlcv_timeframe_valid
        CHECK (timeframe IN ('1m', '3m', '5m', '15m', '30m', '1h', '1d', '1w')),
    CONSTRAINT ohlcv_prices_positive
        CHECK (open > 0 AND high > 0 AND low > 0 AND close > 0),
    CONSTRAINT ohlcv_ohlc_order
        CHECK (high >= GREATEST(open, close, low) AND low <= LEAST(open, close)),
    CONSTRAINT ohlcv_volume_nonneg
        CHECK (volume >= 0 AND (oi IS NULL OR oi >= 0)),
    CONSTRAINT ohlcv_daily_canonical_ts
        CHECK (timeframe <> '1d'
               OR ts = (date_trunc('day', ts AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata'))
);

SELECT create_hypertable('app.ohlcv_candles', by_range('ts'));

-- ---------------------------------------------------------------------------
-- Ticks (D1: tick storage for slippage calibration). Minimal shape; Phase 1 extends it.
-- No uniqueness: several ticks may share a timestamp.
-- ---------------------------------------------------------------------------
CREATE TABLE app.ticks (
    symbol         text        NOT NULL,
    ts             timestamptz NOT NULL,
    last_price     bigint      NOT NULL,
    last_qty       bigint,
    volume_traded  bigint,
    oi             bigint,
    ingested_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ticks_price_positive CHECK (last_price > 0),
    CONSTRAINT ticks_qty_nonneg CHECK (
        (last_qty IS NULL OR last_qty >= 0)
        AND (volume_traded IS NULL OR volume_traded >= 0)
        AND (oi IS NULL OR oi >= 0))
);

SELECT create_hypertable('app.ticks', by_range('ts'));

CREATE INDEX ticks_symbol_ts_idx ON app.ticks (symbol, ts DESC);
