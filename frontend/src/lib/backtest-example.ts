/** Teaching example only. Amounts are invented and are integer paisa. */
export const startingCapital = 10_000_000;
export const exampleTrades = [
  { label: "Trade 1", result: 200_000 },
  { label: "Trade 2", result: -100_000 },
  { label: "Trade 3", result: 150_000 },
  { label: "Trade 4", result: -50_000 },
] as const;

export function summarizeExample(start: number, trades: readonly { result: number }[]) {
  let equity = start;
  let peak = start;
  let maxDrawdown = 0;
  const curve = [start];
  for (const trade of trades) {
    equity += trade.result;
    peak = Math.max(peak, equity);
    maxDrawdown = Math.max(maxDrawdown, (peak - equity) / peak);
    curve.push(equity);
  }
  return {
    curve,
    net: equity - start,
    returnPct: ((equity - start) / start) * 100,
    winRate: trades.length ? (trades.filter((t) => t.result > 0).length / trades.length) * 100 : 0,
    maxDrawdownPct: maxDrawdown * 100,
  };
}
