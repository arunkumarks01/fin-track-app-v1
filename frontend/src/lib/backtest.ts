type Row = Record<string, unknown>;
function row(value: unknown): Row {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Row;
  throw new Error("Invalid backtest record");
}
function integer(value: unknown): number {
  if (typeof value === "number" && Number.isSafeInteger(value)) return value;
  throw new Error("Invalid backtest number");
}
function text(value: unknown): string {
  if (typeof value === "string") return value;
  throw new Error("Invalid backtest text");
}
function list(value: unknown): Row[] {
  if (!Array.isArray(value)) throw new Error("Invalid backtest table");
  return value.map(row);
}
function optionalInteger(value: unknown): number | null {
  return value == null ? null : integer(value);
}
function run(value: unknown) {
  const r = row(value);
  const status = text(r.status);
  if (!["queued", "running", "completed", "failed"].includes(status))
    throw new Error("Unknown backtest status");
  return {
    id: integer(r.id),
    strategy: text(r.strategy_id),
    revision: integer(r.strategy_revision),
    symbol: text(r.symbol),
    start: text(r.start_day),
    end: text(r.end_day),
    status,
    capital: integer(r.capital_paisa),
    detail: r.detail == null ? null : text(r.detail),
    assumptions: row(r.assumptions),
    state: r.result_state == null ? null : row(r.result_state),
  };
}
export function parseRuns(value: unknown) {
  return list(row(value).runs).map((r) => ({
    ...run(r),
    net: optionalInteger(r.net_paisa),
    trades: optionalInteger(r.closed_trades),
  }));
}
export function parseResult(value: unknown) {
  const r = row(value);
  const m = r.metrics == null ? null : row(r.metrics);
  return {
    run: run(r.run),
    metrics:
      m == null
        ? null
        : {
            ending: integer(m.ending_equity_paisa),
            net: integer(m.net_paisa),
            trades: integer(m.closed_trades),
            wins: integer(m.wins),
            losses: integer(m.losses),
            drawdown: integer(m.max_drawdown_numerator) / integer(m.max_drawdown_denominator),
            grossProfit: integer(m.gross_profit_paisa),
            grossLoss: integer(m.gross_loss_paisa),
          },
    fills: list(r.fills).map((f) => ({
      id: integer(f.id),
      day: text(f.trade_day),
      side: text(f.side),
      quantity: integer(f.quantity),
      price: integer(f.price_paisa),
      fees: integer(f.fees_paisa),
      realized: optionalInteger(f.realized_paisa),
      reason: f.reason == null ? "—" : text(f.reason),
    })),
    equity: list(r.equity).map((e) => ({ day: text(e.trade_day), value: integer(e.equity_paisa) })),
  };
}
async function get(path: string): Promise<unknown> {
  const res = await fetch(`/api/backtest${path}`, { cache: "no-store", redirect: "error" });
  if (!res.ok) throw new Error(`Backtest results unavailable (${res.status}).`);
  return res.json();
}
export async function backtestRuns() {
  return parseRuns(await get("/runs"));
}
export async function backtestResult(id: number) {
  return parseResult(await get(`/runs/${id}`));
}
