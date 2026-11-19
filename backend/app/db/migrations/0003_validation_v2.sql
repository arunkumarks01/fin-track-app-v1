-- 0003_validation_v2 — validation rules learned from the first real Kite backfill.
--
-- * Kite's pre-Dec-2018 daily bars for some symbols are stamped 09:15 IST (or odd times)
--   instead of 00:00. They are now re-stamped to the canonical trading-date ts and flagged
--   'retimed_ts' (original ts kept in the flag detail) instead of being rejected.
-- * Indices have no corporate actions, so an implausible day-over-day gap is a data error:
--   new quarantine reason 'index_gap'.
-- * Re-fetching the same bad row no longer piles up duplicate quarantine rows.

ALTER TABLE app.ohlcv_quarantine DROP CONSTRAINT ohlcv_quarantine_reason_check;
ALTER TABLE app.ohlcv_quarantine ADD CONSTRAINT ohlcv_quarantine_reason_check CHECK (reason IN (
    'unparseable', 'non_positive_price', 'ohlc_order', 'negative_volume', 'non_canonical_ts',
    'duplicate_in_batch', 'conflicts_with_stored', 'index_gap'));

ALTER TABLE app.ohlcv_flags DROP CONSTRAINT ohlcv_flags_flag_check;
ALTER TABLE app.ohlcv_flags ADD CONSTRAINT ohlcv_flags_flag_check CHECK (
    flag IN ('large_gap', 'missing_sessions', 'retimed_ts'));

CREATE UNIQUE INDEX ohlcv_quarantine_dedupe
    ON app.ohlcv_quarantine (symbol, timeframe, ts, reason, md5(raw::text)) NULLS NOT DISTINCT;
