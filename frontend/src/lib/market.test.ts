import { describe, expect, it } from "vitest";

import { ChartContractError } from "./charts";
import { fetchDailyCloses, parseDailyCloses, summarize } from "./market";

const page = (closes: number[]) => ({
  prices: "integer paisa",
  source: "Kite daily (indices are not adjusted)",
  has_more: false,
  next_before: null,
  omitted_unusable: 0,
  bars: closes.map((c, i) => ({ t: 1_794_000_000 + i * 86_400, o: c, h: c, l: c, c, v: 0 })),
});

describe("summarize", () => {
  it("computes the change in paisa and basis points", () => {
    const s = summarize(parseDailyCloses("NSE:NIFTY 50", page([2_522_795, 2_551_430])));
    expect(s).toMatchObject({ last: 2_551_430, change: 28_635, changeBps: 114 }); // +1.14%
  });

  it("rounds falls symmetrically and keeps an unchanged day at zero", () => {
    expect(summarize(parseDailyCloses("X", page([10_000, 9_985])))?.changeBps).toBe(-15);
    expect(summarize(parseDailyCloses("X", page([10_000, 10_000])))?.changeBps).toBe(0);
  });

  it("refuses to guess with fewer than two closes", () => {
    expect(summarize(parseDailyCloses("X", page([10_000])))).toBeNull();
  });

  it("rejects a page that isn't integer paisa", () => {
    expect(() => parseDailyCloses("X", { ...page([1, 2]), prices: "rupees" })).toThrow(
      ChartContractError,
    );
  });
});

describe("fetchDailyCloses", () => {
  it("asks for daily bars and reports a failed request", async () => {
    let url = "";
    const f = (async (u: string) => {
      url = u;
      return new Response("", { status: 503 });
    }) as unknown as typeof fetch;
    await expect(fetchDailyCloses("NSE:NIFTY 50", 30, f)).rejects.toThrow("HTTP 503");
    expect(url).toBe("/api/data/candles?symbol=NSE%3ANIFTY+50&interval=D&count=30");
  });
});
