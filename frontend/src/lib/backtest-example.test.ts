import { describe, expect, it } from "vitest";

import { exampleTrades, startingCapital, summarizeExample } from "./backtest-example";

describe("the teaching example", () => {
  it("derives the displayed figures from the same hypothetical outcomes", () => {
    const result = summarizeExample(startingCapital, exampleTrades);
    expect(result.curve).toEqual([10_000_000, 10_200_000, 10_100_000, 10_250_000, 10_200_000]);
    expect(result.net).toBe(200_000);
    expect(result.returnPct).toBe(2);
    expect(result.winRate).toBe(50);
    expect(result.maxDrawdownPct).toBeCloseTo(100 / 102, 4);
  });
});
