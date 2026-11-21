/**
 * Plain-English labels for the data codes the backend reports — one place, so the Admin page
 * and the Stock page always say the same thing (UX_DESIGN: plain words, no per-page wording).
 */

export const QUARANTINE_REASONS: Record<string, string> = {
  non_canonical_ts: "timestamp not on the minute / day",
  non_positive_price: "zero or negative price",
  ohlc_order: "high/low out of order",
  negative_volume: "negative volume",
  out_of_range: "value too large to store",
  unparseable: "unparseable record",
  duplicate_in_batch: "same date twice in one batch",
  conflicts_with_stored: "differs from the stored bar",
  superseded_provisional: "provisional bar replaced by the final one (repair record)",
  index_gap: "index jump too large",
};

export const FLAG_LABELS: Record<string, string> = {
  large_gap: "big jump from the previous close",
  missing_sessions: "trading days missing before it",
  retimed_ts: "timestamp moved to the trading day",
  official_mismatch: "one day off NSE's official price",
  pre_listing: "before the real listing (not counted)",
};

export const QUALITY_TEXT: Record<string, string> = {
  measured: "measured against NSE",
  carried: "carried (NSE row missing, flat window)",
  blip: "blip (one day off its window)",
  suspect: "suspect (no as-traded derived)",
  underdetermined: "under-determined window (too few days)",
  unmeasurable: "unmeasurable",
  not_applicable: "indices (never adjusted)",
};

export function label(map: Record<string, string>, code: string): string {
  return map[code] ?? code.replaceAll("_", " ");
}
