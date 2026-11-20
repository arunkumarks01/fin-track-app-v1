-- 0006_affine_calendar — two corrections found on real data (2026-11-20), both approved.
--
-- 1. Kite's adjustment is AFFINE, not a ratio: for part of history Kite subtracts dividends
--    (ASHOKLEY 2018: Kite = 0.4998·NSE − ₹2.45). adjustment_days gains the offset b (paisa);
--    f_daily is the slope a. As-traded = (Kite ÷ G − b) ÷ a. Windows that are too short to
--    determine a line are 'underdetermined' (no as-traded derived).
-- 2. Session completeness is judged PER INSTRUMENT against its own norm (revised DEC-1B):
--    a cross-sectional benchmark fails on outage days and on the phased 2026 closing-auction
--    rollout (equities 375 → 360 minutes on different dates). app.intraday_session_status
--    stores each session's expected length and status.
-- Also: bars before a symbol's verified listing are flagged 'pre_listing' (kept, visible).

ALTER TABLE app.adjustment_days ADD COLUMN b_daily numeric;

ALTER TABLE app.adjustment_days DROP CONSTRAINT adjustment_days_quality_check;
ALTER TABLE app.adjustment_days ADD CONSTRAINT adjustment_days_quality_check CHECK (quality IN (
    'measured', 'carried', 'blip', 'suspect', 'unmeasurable', 'not_applicable',
    'underdetermined'));

ALTER TABLE app.ohlcv_flags DROP CONSTRAINT ohlcv_flags_flag_check;
ALTER TABLE app.ohlcv_flags ADD CONSTRAINT ohlcv_flags_flag_check CHECK (
    flag IN ('large_gap', 'missing_sessions', 'retimed_ts', 'official_mismatch', 'pre_listing'));

-- As-traded 1-minute bars: prices = (stored ÷ g_minute − b_daily) ÷ f_daily, volume = stored ÷
-- (v_daily × v_minute), rounded (paisa / shares). NULL — never guessed — unless the day's
-- quality is measured, carried or not_applicable (indices).
CREATE OR REPLACE FUNCTION app.minute_as_traded(p_symbol text, p_from timestamptz, p_to timestamptz)
RETURNS TABLE (ts timestamptz, open bigint, high bigint, low bigint, close bigint,
               volume bigint, quality text)
LANGUAGE sql STABLE AS $$
    SELECT m.ts,
           CASE WHEN ok THEN round((m.open / g - b) / a)::bigint END,
           CASE WHEN ok THEN round((m.high / g - b) / a)::bigint END,
           CASE WHEN ok THEN round((m.low / g - b) / a)::bigint END,
           CASE WHEN ok THEN round((m.close / g - b) / a)::bigint END,
           CASE WHEN ok AND kv IS NOT NULL THEN round(m.volume / kv)::bigint END,
           COALESCE(ad.quality, 'unmeasurable')
    FROM app.ohlcv_1m m
    LEFT JOIN app.adjustment_days ad
           ON ad.symbol = p_symbol AND ad.trade_date = (m.ts AT TIME ZONE 'Asia/Kolkata')::date
    CROSS JOIN LATERAL (SELECT
        ad.quality IN ('measured', 'carried', 'not_applicable')
            AND ad.g_minute IS NOT NULL AND ad.f_daily IS NOT NULL AS ok,
        ad.f_daily::numeric AS a,
        COALESCE(ad.b_daily, 0)::numeric AS b,
        ad.g_minute::numeric AS g,
        (ad.v_daily * ad.v_minute)::numeric AS kv) x
    WHERE m.sid = (SELECT sid FROM app.instrument_ids WHERE symbol = p_symbol)
      AND m.ts >= p_from AND m.ts < p_to
    ORDER BY m.ts
$$;

-- Per-instrument session completeness (revised DEC-1B). expected = the instrument's own norm
-- that session (re-baselined on a persistent step-change); market_short = the exchange had a
-- market-wide short day (outage / muhurat / half-day) and this session was short with it —
-- reported, never flagged; unknown = no norm established yet.
CREATE TABLE app.intraday_session_status (
    sid           smallint NOT NULL,
    session_date  date     NOT NULL,
    bars          integer  NOT NULL CHECK (bars > 0),
    expected      integer  CHECK (expected > 0),
    status        text     NOT NULL CHECK (status IN (
                      'complete', 'short', 'excess', 'market_short', 'unknown')),
    PRIMARY KEY (sid, session_date),
    CONSTRAINT intraday_session_status_known CHECK ((status = 'unknown') = (expected IS NULL))
);
