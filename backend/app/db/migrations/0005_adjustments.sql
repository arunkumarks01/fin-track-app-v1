-- 0005_adjustments — Phase 1c: measure, don't model (DEC-B revised, ARCHITECTURE §5).
--
-- Kite's candles arrive ALREADY adjusted (splits, bonuses and more — e.g. some dividends and
-- rights), and Kite publishes no corporate-actions list. So:
--   * adjusted view  = Kite's data, stored unchanged (ohlcv_candles / ohlcv_1m);
--   * as-traded daily = NSE's official bhavcopy (app.nse_bhav), exact — never reconstructed;
--   * Kite's factor   = Kite close ÷ NSE official close, MEASURED per symbol per day, flat
--     inside windows bounded by NSE ex-dates and measured jumps (app.adjustment_days);
--   * the 1m grain gets its own measured ratio to the daily grain (they can differ — e.g.
--     RELIANCE 1m/daily = 0.991 until the 2020-05-13 rights ex-date, 1.000 after);
--   * every jump is attributed to an NSE action or flagged (app.adjustment_events);
--   * Kite re-adjusting history after we stored it is detected on re-fetch (app.adjustment_drift).

-- Per-session 1m aggregates (kept by the extractor; backfilled by the adjustment analysis
-- once) so measuring the 1m/daily ratio never rescans ohlcv_1m.
ALTER TABLE app.intraday_sessions
    ADD COLUMN open_first  integer,
    ADD COLUMN high_max    integer,
    ADD COLUMN low_min     integer,
    ADD COLUMN volume_sum  bigint;

-- One row per trading date's NSE archive zip (PRddmmyy.zip): the load ledger, resumable.
CREATE TABLE app.nse_archive_files (
    trade_date  date        PRIMARY KEY,
    status      text        NOT NULL CHECK (status IN ('ok', 'missing', 'error')),
    bc_rows     integer     NOT NULL DEFAULT 0 CHECK (bc_rows >= 0),
    pd_rows     integer     NOT NULL DEFAULT 0 CHECK (pd_rows >= 0),
    rejected    integer     NOT NULL DEFAULT 0 CHECK (rejected >= 0),
    detail      text,
    fetched_at  timestamptz NOT NULL
);

-- NSE corporate actions (the Bc file), universe symbols only. PURPOSE is free text: parsed
-- into components where it follows a known pattern, otherwise kept as kind 'other' — stored,
-- never applied. Nothing here is used to MODEL prices; it explains measured factor jumps.
CREATE TABLE app.corporate_actions (
    id              bigserial   PRIMARY KEY,
    symbol          text        NOT NULL,           -- NSE:SYMBOL
    series          text        NOT NULL,
    ex_date         date        NOT NULL,
    record_date     date,
    purpose         text        NOT NULL,           -- whitespace-normalised, upper case
    kinds           text[]      NOT NULL,           -- subset of split/bonus/dividend/other
    split_from_paisa bigint     CHECK (split_from_paisa > 0),   -- face value before
    split_to_paisa   bigint     CHECK (split_to_paisa > 0),     -- face value after
    bonus_new       integer     CHECK (bonus_new > 0),          -- BONUS new:held
    bonus_held      integer     CHECK (bonus_held > 0),
    dividend_paisa  bigint      CHECK (dividend_paisa >= 0),    -- per share, when stated
    first_seen      date        NOT NULL,           -- trade_date of the first file listing it
    CONSTRAINT corporate_actions_kinds CHECK (
        kinds <@ ARRAY['split', 'bonus', 'dividend', 'other']::text[] AND cardinality(kinds) > 0),
    CONSTRAINT corporate_actions_split_pair CHECK (
        (split_from_paisa IS NULL) = (split_to_paisa IS NULL)),
    CONSTRAINT corporate_actions_bonus_pair CHECK ((bonus_new IS NULL) = (bonus_held IS NULL)),
    UNIQUE (symbol, series, ex_date, purpose)
);
CREATE INDEX corporate_actions_symbol_ex ON app.corporate_actions (symbol, ex_date);

-- NSE official as-traded daily prices (the Pd file, series EQ), universe symbols only.
-- Integer paisa; volume = shares traded. THIS is the as-traded daily view.
CREATE TABLE app.nse_bhav (
    symbol      text    NOT NULL,                   -- NSE:SYMBOL
    trade_date  date    NOT NULL,
    prev_close  bigint  NOT NULL CHECK (prev_close > 0),
    open        bigint  NOT NULL CHECK (open > 0),
    high        bigint  NOT NULL CHECK (high > 0),
    low         bigint  NOT NULL CHECK (low > 0),
    close       bigint  NOT NULL CHECK (close > 0),
    volume      bigint  NOT NULL CHECK (volume >= 0),
    PRIMARY KEY (symbol, trade_date),
    CONSTRAINT nse_bhav_ohlc_order CHECK (high >= GREATEST(open, close, low) AND low <= LEAST(open, close))
);

-- Measured Kite adjustment, per symbol per trading day (recomputed per symbol, atomically).
--   f_daily  Kite 1d price = NSE as-traded price × f_daily   (window median, not the raw day)
--   g_minute Kite 1m price = Kite-1d-basis price × g_minute  (window median; NULL = no 1m)
--   v_daily  Kite 1d volume = NSE traded qty × v_daily  (volume has its OWN factor: Kite scales
--            it for share-count events only — e.g. ASHOKLEY 2016 price ×0.4763, volume ×2.0)
--   v_minute Kite 1m volume = Kite-1d-basis volume × v_minute (window median)
--   f_day    that day's own raw ratio (NULL when unmeasurable)
--   quality  measured | carried (no NSE row, inside a flat window) | blip (day deviates from
--            its window) | suspect (window next to a large unexplained jump) | unmeasurable |
--            not_applicable (indices: never adjusted, factors are 1)
CREATE TABLE app.adjustment_days (
    symbol      text    NOT NULL,
    trade_date  date    NOT NULL,
    window_no   integer NOT NULL CHECK (window_no >= 0),
    f_day       numeric,
    f_daily     numeric CHECK (f_daily > 0),
    g_minute    numeric CHECK (g_minute > 0),
    v_daily     numeric CHECK (v_daily > 0),
    v_minute    numeric CHECK (v_minute > 0),
    quality     text    NOT NULL CHECK (quality IN (
                    'measured', 'carried', 'blip', 'suspect', 'unmeasurable', 'not_applicable')),
    PRIMARY KEY (symbol, trade_date),
    CONSTRAINT adjustment_days_usable CHECK (
        quality NOT IN ('measured', 'carried', 'not_applicable') OR f_daily IS NOT NULL)
);

-- Each boundary between windows: where Kite's factor (or the 1m/daily ratio) steps.
--   ratio = f_daily(before) / f_daily(after); g_ratio likewise for the 1m grain.
CREATE TABLE app.adjustment_events (
    symbol          text     NOT NULL,
    effective_date  date     NOT NULL,              -- first trading day of the new window
    ratio           numeric  NOT NULL CHECK (ratio > 0),
    g_ratio         numeric  CHECK (g_ratio > 0),
    action_ids      bigint[] NOT NULL DEFAULT '{}',
    classification  text     NOT NULL CHECK (classification IN (
                        'explained', 'attributed', 'unexplained_small', 'suspect',
                        'official_mismatch')),
    detail          text     NOT NULL,
    PRIMARY KEY (symbol, effective_date)
);

-- Kite re-adjusted history AFTER we stored it: seen when the newest stored bar is re-fetched
-- and comes back scaled by one consistent ratio. Stored bars are never rewritten; the
-- adjusted-as-of-now view multiplies bars on/before ref_date by the ratio.
CREATE TABLE app.adjustment_drift (
    symbol       text        NOT NULL,
    timeframe    text        NOT NULL CHECK (timeframe IN ('1d', '1m')),
    ref_date     date        NOT NULL,              -- stored bars on/before this date are affected
    ratio        numeric     NOT NULL CHECK (ratio > 0),   -- fresh / stored
    detected_at  timestamptz NOT NULL,
    PRIMARY KEY (symbol, timeframe, ref_date)
);

-- Last successful drift check per symbol and grain: the adjusted view is "may be stale"
-- for any symbol whose check is older than the last final session.
CREATE TABLE app.adjustment_checks (
    symbol      text        NOT NULL,
    timeframe   text        NOT NULL CHECK (timeframe IN ('1d', '1m')),
    checked_on  date        NOT NULL,               -- the stored session that was re-fetched
    checked_at  timestamptz NOT NULL,
    PRIMARY KEY (symbol, timeframe)
);

-- Audited corrections of stored bars (the only path that rewrites a stored bar). The old
-- values also go to quarantine ('superseded_provisional') as the rollback record.
CREATE TABLE app.data_repairs (
    id           bigserial   PRIMARY KEY,
    symbol       text        NOT NULL,
    timeframe    text        NOT NULL,
    ts           timestamptz NOT NULL,
    old_values   jsonb       NOT NULL,
    new_values   jsonb       NOT NULL,
    reason       text        NOT NULL,
    repaired_at  timestamptz NOT NULL
);

ALTER TABLE app.ohlcv_quarantine DROP CONSTRAINT ohlcv_quarantine_reason_check;
ALTER TABLE app.ohlcv_quarantine ADD CONSTRAINT ohlcv_quarantine_reason_check CHECK (reason IN (
    'unparseable', 'non_positive_price', 'ohlc_order', 'negative_volume', 'non_canonical_ts',
    'duplicate_in_batch', 'conflicts_with_stored', 'index_gap', 'out_of_range',
    'superseded_provisional'));

ALTER TABLE app.ohlcv_flags DROP CONSTRAINT ohlcv_flags_flag_check;
ALTER TABLE app.ohlcv_flags ADD CONSTRAINT ohlcv_flags_flag_check CHECK (
    flag IN ('large_gap', 'missing_sessions', 'retimed_ts', 'official_mismatch'));

-- As-traded 1-minute bars: prices = stored ÷ (f_daily × g_minute), volume = stored ÷
-- (v_daily × v_minute), of that day, rounded (paisa / shares).
-- Prices are NULL (never guessed) unless the day's quality is measured, carried or
-- not_applicable (indices).
CREATE FUNCTION app.minute_as_traded(p_symbol text, p_from timestamptz, p_to timestamptz)
RETURNS TABLE (ts timestamptz, open bigint, high bigint, low bigint, close bigint,
               volume bigint, quality text)
LANGUAGE sql STABLE AS $$
    SELECT m.ts,
           CASE WHEN ok THEN round(m.open / k)::bigint END,
           CASE WHEN ok THEN round(m.high / k)::bigint END,
           CASE WHEN ok THEN round(m.low / k)::bigint END,
           CASE WHEN ok THEN round(m.close / k)::bigint END,
           CASE WHEN ok AND kv IS NOT NULL THEN round(m.volume / kv)::bigint END,
           COALESCE(a.quality, 'unmeasurable')
    FROM app.ohlcv_1m m
    LEFT JOIN app.adjustment_days a
           ON a.symbol = p_symbol AND a.trade_date = (m.ts AT TIME ZONE 'Asia/Kolkata')::date
    CROSS JOIN LATERAL (SELECT
        a.quality IN ('measured', 'carried', 'not_applicable') AND a.g_minute IS NOT NULL AS ok,
        (a.f_daily * a.g_minute)::numeric AS k,
        (a.v_daily * a.v_minute)::numeric AS kv) x
    WHERE m.sid = (SELECT sid FROM app.instrument_ids WHERE symbol = p_symbol)
      AND m.ts >= p_from AND m.ts < p_to
    ORDER BY m.ts
$$;

-- Product of drift ratios that apply to a bar of `p_date` (1.0 when Kite has not re-adjusted
-- since we stored it). Adjusted-as-of-now = stored × this.
CREATE FUNCTION app.drift_factor(p_symbol text, p_timeframe text, p_date date)
RETURNS numeric LANGUAGE sql STABLE AS $$
    SELECT COALESCE(exp(sum(ln(ratio))), 1)::numeric
    FROM app.adjustment_drift
    WHERE symbol = p_symbol AND timeframe = p_timeframe AND ref_date >= p_date
$$;
