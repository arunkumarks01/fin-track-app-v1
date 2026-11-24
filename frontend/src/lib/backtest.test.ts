import { describe, expect, it } from "vitest";
import { parseResult, parseRuns } from "./backtest";

const run = {
  id: 1,
  strategy_id: "rsi_bounce_long",
  strategy_revision: 1,
  symbol: "NSE:AAA",
  start_day: "2025-01-01",
  end_day: "2025-12-31",
  capital_paisa: 1000000,
  status: "failed",
  detail: "Incomplete coverage",
  assumptions: { quantity: 10 },
  result_state: null,
};

describe("persisted backtest responses", () => {
  it("keeps failed runs distinct from a zero-return result", () => {
    const rows = parseRuns({ runs: [{ ...run, net_paisa: null, closed_trades: null }] });
    expect(rows[0]!.net).toBeNull();
    expect(rows[0]!.status).toBe("failed");
    const result = parseResult({ run, metrics: null, fills: [], equity: [] });
    expect(result.metrics).toBeNull();
    expect(result.run.detail).toBe("Incomplete coverage");
  });
  it("rejects unsafe money and unknown status", () => {
    expect(() => parseRuns({ runs: [{ ...run, capital_paisa: 1.5 }] })).toThrow();
    expect(() => parseRuns({ runs: [{ ...run, status: "successful" }] })).toThrow();
  });
  it("preserves marked equity, costs and partial realized fills", () => {
    const result = parseResult({
      run: { ...run, status: "completed", result_state: { quantity: 6 } },
      metrics: {
        ending_equity_paisa: 990000,
        net_paisa: -10000,
        closed_trades: 0,
        wins: 0,
        losses: 0,
        max_drawdown_numerator: 1,
        max_drawdown_denominator: 100,
        gross_profit_paisa: 0,
        gross_loss_paisa: 0,
      },
      fills: [
        {
          id: 1,
          trade_day: "2025-01-02",
          side: "sell",
          quantity: 4,
          price_paisa: 10000,
          fees_paisa: 100,
          realized_paisa: -500,
          reason: "stop_hit",
        },
      ],
      equity: [{ trade_day: "2025-01-02", equity_paisa: 990000 }],
    });
    expect(result.metrics?.drawdown).toBe(0.01);
    expect(result.fills[0]!.fees).toBe(100);
    expect(result.run.state?.quantity).toBe(6);
  });
});
