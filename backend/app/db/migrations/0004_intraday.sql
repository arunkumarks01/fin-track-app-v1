-- 0004_intraday — the single stored intraday source (1-minute) + exchange session calendar
-- (Phase 1b, DEC-1B). Higher intraday timeframes are DERIVED on read by app.intraday_bars();
-- nothing else is stored. 1d stays Kite's native daily bar (official close ≠ last 1m close).

-- Stable local id for universe symbols. Never reused or deleted. smallint is safe only
-- because it maps the ~224-symbol universe, NOT Kite's instrument_token (bigint, 56k master).
CREATE TABLE app.instrument_ids (
    sid         smallint    GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    symbol      text        NOT NULL UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Compact 1-minute bars (~90 B/row incl. PK vs ~190 B in ohlcv_candles). Prices are integer
-- paisa in int4 (max ₹2.1 crore/share). Columns ordered 8-byte first to avoid padding.
CREATE TABLE app.ohlcv_1m (
    ts      timestamptz NOT NULL,
    volume  bigint      NOT NULL,
    open    integer     NOT NULL,
    high    integer     NOT NULL,
    low     integer     NOT NULL,
    close   integer     NOT NULL,
    sid     smallint    NOT NULL,
    CONSTRAINT ohlcv_1m_pk PRIMARY KEY (sid, ts),
    CONSTRAINT ohlcv_1m_minute_aligned CHECK (date_trunc('minute', ts) = ts),
    CONSTRAINT ohlcv_1m_prices_positive CHECK (open > 0 AND high > 0 AND low > 0 AND close > 0),
    CONSTRAINT ohlcv_1m_ohlc_order
        CHECK (high >= GREATEST(open, close, low) AND low <= LEAST(open, close)),
    CONSTRAINT ohlcv_1m_volume_nonneg CHECK (volume >= 0)
);
-- no default ts index: the PK (sid, ts) serves every access path and saves ~30 B/row
SELECT create_hypertable('app.ohlcv_1m', by_range('ts'), create_default_indexes => false);

-- Per-symbol per-session minute counts, maintained by the extractor (cheap completeness).
CREATE TABLE app.intraday_sessions (
    sid           smallint    NOT NULL,
    session_date  date        NOT NULL,
    bars          integer     NOT NULL CHECK (bars > 0),
    first_ts      timestamptz NOT NULL,
    last_ts       timestamptz NOT NULL,
    PRIMARY KEY (sid, session_date)
);

-- Derived exchange calendar: each session's real open (earliest first minute across the
-- exchange's symbols, so one missing minute can't shift it) and expected minute count (the
-- exchange benchmark's bars that day — muhurat/half-days/special sessions are not 375).
-- NULL expected_minutes = benchmark has no data that day = completeness unknown, not guessed.
CREATE TABLE app.exchange_sessions (
    exchange          text        NOT NULL,
    session_date      date        NOT NULL,
    session_open      timestamptz NOT NULL,
    expected_minutes  integer     CHECK (expected_minutes IS NULL OR expected_minutes > 0),
    benchmark         text        NOT NULL,
    refreshed_at      timestamptz NOT NULL,
    PRIMARY KEY (exchange, session_date)
);

-- Newest-first backfill state per symbol.
CREATE TABLE app.intraday_cursor (
    sid            smallint    PRIMARY KEY,
    newest_ts      timestamptz,
    oldest_date    date,
    floor_reached  boolean     NOT NULL DEFAULT false,
    empty_windows  integer     NOT NULL DEFAULT 0 CHECK (empty_windows >= 0),
    status         text        NOT NULL CHECK (status IN ('ok', 'error')),
    last_error     text,
    rows_written   bigint      NOT NULL DEFAULT 0 CHECK (rows_written >= 0),
    updated_at     timestamptz NOT NULL
);

ALTER TABLE app.ohlcv_quarantine DROP CONSTRAINT ohlcv_quarantine_reason_check;
ALTER TABLE app.ohlcv_quarantine ADD CONSTRAINT ohlcv_quarantine_reason_check CHECK (reason IN (
    'unparseable', 'non_positive_price', 'ohlc_order', 'negative_volume', 'non_canonical_ts',
    'duplicate_in_batch', 'conflicts_with_stored', 'index_gap', 'out_of_range'));

-- THE one derivation of intraday timeframes, shared by live scanning and backtesting
-- (parity contract). Buckets are anchored to each session's actual open in IST — never to
-- midnight (which misaligns 30m/1h) and never to a fixed 09:15 (which misaligns muhurat
-- sessions). Golden-tested to reproduce Kite's native 5m/15m/30m/60m bars exactly.
-- session_open is NULL in the output when the calendar has no row for that date (then the
-- regular 09:15 open is assumed and the caller can see it).
CREATE FUNCTION app.intraday_bars(
    p_symbol text, p_width interval, p_from timestamptz, p_to timestamptz)
RETURNS TABLE (bucket timestamptz, open integer, high integer, low integer, close integer,
               volume bigint, minutes integer, session_open timestamptz)
LANGUAGE sql STABLE AS $$
    SELECT time_bucket(
               p_width, m.ts, 'Asia/Kolkata',
               COALESCE(s.session_open,
                        ((m.ts AT TIME ZONE 'Asia/Kolkata')::date + TIME '09:15')
                            AT TIME ZONE 'Asia/Kolkata')) AS bucket,
           first(m.open, m.ts), max(m.high), min(m.low), last(m.close, m.ts),
           sum(m.volume)::bigint, count(*)::integer, s.session_open
    FROM app.ohlcv_1m m
    LEFT JOIN app.exchange_sessions s
           ON s.exchange = split_part(p_symbol, ':', 1)
          AND s.session_date = (m.ts AT TIME ZONE 'Asia/Kolkata')::date
    WHERE m.sid = (SELECT sid FROM app.instrument_ids WHERE symbol = p_symbol)
      AND m.ts >= p_from AND m.ts < p_to
    GROUP BY 1, s.session_open
    ORDER BY 1
$$;
