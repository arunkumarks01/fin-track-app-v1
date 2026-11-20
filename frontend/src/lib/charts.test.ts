import { describe, expect, it } from "vitest";

import { ChartContractError, FinTrackFeed, parseCandles, parseSymbols } from "./charts";

const PAGE = {
  symbol: "NSE:AAA",
  interval: "D",
  adjustment: "adjusted",
  source: "Kite daily (adjusted)",
  prices: "integer paisa",
  bars: [
    { t: 1763663400, o: 10000, h: 10050, l: 9950, c: 10010, v: 1000 },
    { t: 1763749800, o: 10100, h: 10150, l: 10050, c: 10110, v: 1001 },
  ],
  has_more: true,
  next_before: 1763663400,
  omitted_unusable: 0,
};

const SYMBOLS = {
  symbols: [
    { symbol: "NSE:RELIANCE", exchange: "NSE", tradingsymbol: "RELIANCE", kind: "equity" },
    { symbol: "NSE:NIFTY 50", exchange: "NSE", tradingsymbol: "NIFTY 50", kind: "index" },
    { symbol: "NSE:HDFCBANK", exchange: "NSE", tradingsymbol: "HDFCBANK", kind: "equity" },
  ],
};

describe("parseCandles", () => {
  it("converts paisa to rupees only here, keeps times and paging", () => {
    const p = parseCandles(PAGE);
    expect(p.bars[0]).toEqual({
      time: 1763663400,
      open: 100,
      high: 100.5,
      low: 99.5,
      close: 100.1,
      volume: 1000,
    });
    expect([p.hasMore, p.nextBefore, p.source]).toEqual([
      true,
      1763663400,
      "Kite daily (adjusted)",
    ]);
  });

  it.each([
    ["a non-paisa price unit", { ...PAGE, prices: "rupees" }],
    ["a fractional price", { ...PAGE, bars: [{ ...PAGE.bars[0], c: 100.5 }] }],
    ["high below close", { ...PAGE, bars: [{ ...PAGE.bars[0], h: 10000, c: 10010 }] }],
    ["descending times", { ...PAGE, bars: [PAGE.bars[1], PAGE.bars[0]] }],
    ["a missing has_more", { ...PAGE, has_more: undefined }],
  ])("rejects %s", (_n, body) => {
    expect(() => parseCandles(body)).toThrow(ChartContractError);
  });
});

describe("parseSymbols", () => {
  it("accepts equities and indices only", () => {
    expect(parseSymbols(SYMBOLS)).toHaveLength(3);
    const bad = { symbols: [{ ...SYMBOLS.symbols[0], kind: "crypto" }] };
    expect(() => parseSymbols(bad)).toThrow(ChartContractError);
  });
});

function fakeFetch(routes: Record<string, unknown>, seen: string[] = []): typeof fetch {
  return (async (url: string) => {
    seen.push(url);
    const key = Object.keys(routes).find((k) => url.startsWith(k));
    if (!key) return new Response(JSON.stringify({ detail: "nope" }), { status: 422 });
    return new Response(JSON.stringify(routes[key]), { status: 200 });
  }) as unknown as typeof fetch;
}

describe("FinTrackFeed", () => {
  it("asks FinTrack for the interval itself, pages backwards, defaults to adjusted", async () => {
    const seen: string[] = [];
    const feed = new FinTrackFeed(fakeFetch({ "/api/data/candles": PAGE }, seen), 5000);
    const page = await feed.getBarsPage({
      symbol: "RELIANCE",
      exchange: "NSE",
      interval: "5m",
      before: 1763700000,
      countBack: 300,
    });
    const q = new URL(seen[0] ?? "", "http://x").searchParams;
    expect([
      q.get("symbol"),
      q.get("interval"),
      q.get("adjustment"),
      q.get("before"),
      q.get("count"),
    ]).toEqual(["NSE:RELIANCE", "5m", "adjusted", "1763700000", "300"]);
    expect([page.hasMore, page.nextBefore, page.bars.length]).toEqual([true, 1763663400, 2]);
  });

  it("caps a page at the configured maximum and passes the raw variant through", async () => {
    const seen: string[] = [];
    const feed = new FinTrackFeed(fakeFetch({ "/api/data/candles": PAGE }, seen), 5000);
    await feed.getBars({
      symbol: "RELIANCE",
      exchange: "NSE",
      interval: "D",
      countBack: 999999,
      variant: { adjustment: "raw" },
    });
    const q = new URL(seen[0] ?? "", "http://x").searchParams;
    expect([q.get("count"), q.get("adjustment")]).toEqual(["5000", "raw"]);
  });

  it("declares as-traded only where FinTrack has it", async () => {
    const feed = new FinTrackFeed(fakeFetch({ "/api/data/symbols": SYMBOLS }));
    const q = (symbol: string, interval: string) =>
      feed.dataVariants({ symbol, exchange: "NSE", interval });
    expect(await q("RELIANCE", "D")).toEqual({ adjustments: ["adjusted", "raw"] });
    expect(await q("RELIANCE", "1m")).toEqual({ adjustments: ["adjusted", "raw"] });
    expect(await q("RELIANCE", "5m")).toEqual({}); // derived: no as-traded series
    expect(await q("NIFTY 50", "D")).toEqual({}); // indices: one series
  });

  it("searches the universe, prefix matches first", async () => {
    const feed = new FinTrackFeed(fakeFetch({ "/api/data/symbols": SYMBOLS }));
    const hits = await feed.searchSymbols({ query: "ban" });
    expect(hits.map((h) => h.symbol)).toEqual(["HDFCBANK"]);
    expect((await feed.searchSymbols({ query: "n" })).map((h) => h.symbol)[0]).toBe("NIFTY 50");
  });

  it("surfaces a refusal with the backend's reason", async () => {
    const feed = new FinTrackFeed(fakeFetch({}));
    await expect(
      feed.getBars({
        symbol: "RELIANCE",
        exchange: "NSE",
        interval: "5m",
        variant: { adjustment: "raw" },
      }),
    ).rejects.toThrow("HTTP 422 — nope");
  });
});
