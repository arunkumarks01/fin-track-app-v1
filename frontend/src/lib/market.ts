/**
 * Daily closes for the Dashboard, from FinTrack's own stored candles (/api/data/candles).
 * The page is validated by the strict chart parser; closes stay integer paisa, and the change
 * is computed in paisa — rupees/points appear only when formatted for display.
 */

import { parseCandles } from "./charts";

export type DailyCloses = {
  symbol: string;
  times: number[]; // unix seconds, ascending
  closes: number[]; // integer paisa
  source: string;
};

export type CloseSummary = {
  last: number;
  lastTime: number;
  change: number; // paisa vs the previous close
  changeBps: number; // basis points, rounded half away from zero
};

export function parseDailyCloses(symbol: string, body: unknown): DailyCloses {
  const page = parseCandles(body); // shape, integer paisa, OHLC order, ascending times
  const raw = (body as { bars: { c: number }[] }).bars;
  return {
    symbol,
    times: page.bars.map((b) => b.time),
    closes: raw.map((b) => b.c),
    source: page.source,
  };
}

/** Last close and its change vs the previous one; null with fewer than two closes. */
export function summarize(d: DailyCloses): CloseSummary | null {
  const n = d.closes.length;
  if (n < 2) return null;
  const last = d.closes[n - 1]!;
  const prev = d.closes[n - 2]!;
  if (prev <= 0) return null;
  const change = last - prev;
  const bps = (change * 10_000) / prev;
  return {
    last,
    lastTime: d.times[n - 1]!,
    change,
    changeBps: Math.sign(bps) * Math.round(Math.abs(bps)),
  };
}

export async function fetchDailyCloses(
  symbol: string,
  count: number,
  f: typeof fetch = fetch,
): Promise<DailyCloses> {
  const q = new URLSearchParams({ symbol, interval: "D", count: String(count) });
  const res = await f(`/api/data/candles?${q}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`candles for ${symbol} failed: HTTP ${res.status}`);
  return parseDailyCloses(symbol, await res.json());
}
