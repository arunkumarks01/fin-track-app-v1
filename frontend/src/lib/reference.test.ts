import { describe, expect, it } from "vitest";

import {
  type Positions,
  fetchReference,
  longShareBps,
  nets,
  parseReference,
  ReferenceContractError,
} from "./reference";

const P = (over: Partial<Positions> = {}): Positions => ({
  fut_idx_long: 75864,
  fut_idx_short: 146318,
  fut_stk_long: 3673303,
  fut_stk_short: 2671683,
  opt_idx_call_long: 882959,
  opt_idx_put_long: 1425970,
  opt_idx_call_short: 992566,
  opt_idx_put_short: 1041238,
  opt_stk_call_long: 323523,
  opt_stk_put_long: 422818,
  opt_stk_call_short: 439422,
  opt_stk_put_short: 345424,
  total_long: 6804437,
  total_short: 5636651,
  ...over,
});
const LEDGER = {
  ok: 2946,
  missing: 0,
  error: 0,
  oldest: "2015-01-01",
  newest: "2026-11-20",
  rejected: 0,
  rounding_days: 0,
};
const BODY = {
  ledger: {
    mto: LEDGER,
    participant_oi: { ...LEDGER, rounding_days: 1180 },
    participant_vol: LEDGER,
    bulk_deals: { ...LEDGER, ok: 1, missing: 1 },
    block_deals: { ...LEDGER, ok: 1 },
    pr_text: LEDGER,
    market_cap: { ...LEDGER, oldest: "2024-02-01" },
  },
  participants: [{ date: "2026-11-20", oi: { FII: P() }, volume: {} }],
  sources: {
    news_items: 10,
    news_errors: 0,
    news_refusals: 1,
    calendar_upcoming: 2,
    fred_series: 0,
    fred_errors: 0,
    fred_key_configured: false,
    gift_nifty: {
      daily_bars: 1460,
      daily_date: "2026-11-20",
      minute_bars: 49231,
      minute_at: "2026-11-20T17:22:00+00:00",
    },
    india_vix: { daily_bars: 0, daily_date: null, minute_bars: 0, minute_at: null },
  },
};

describe("parseReference", () => {
  it("maps the ledger and positions", () => {
    const r = parseReference(BODY);
    expect(r.ledger.participant_oi.roundingDays).toBe(1180);
    expect(r.ledger.bulk_deals.missing).toBe(1);
    expect(r.participants[0]?.oi.FII?.fut_idx_short).toBe(146318);
    expect(r.sources.newsRefusals).toBe(1);
    expect(r.sources.fredKeyConfigured).toBe(false);
    expect(r.sources.giftNifty.dailyBars).toBe(1460);
    expect(r.sources.indiaVix.minuteAt).toBeNull();
  });

  it("rejects unknown participants, negative or fractional counts, and missing datasets", () => {
    const withX = { ...BODY, participants: [{ date: "2026-11-20", oi: { X: P() }, volume: {} }] };
    expect(() => parseReference(withX)).toThrow(ReferenceContractError);
    const neg = {
      ...BODY,
      participants: [{ date: "2026-11-20", oi: { FII: P({ fut_idx_long: -1 }) }, volume: {} }],
    };
    expect(() => parseReference(neg)).toThrow(ReferenceContractError);
    const frac = {
      ...BODY,
      participants: [{ date: "2026-11-20", oi: { FII: P({ fut_idx_long: 1.5 }) }, volume: {} }],
    };
    expect(() => parseReference(frac)).toThrow(ReferenceContractError);
    const rest: Record<string, unknown> = { ...BODY.ledger };
    delete rest.mto;
    expect(() => parseReference({ ...BODY, ledger: rest })).toThrow(ReferenceContractError);
  });
});

describe("nets", () => {
  it("is long minus short in whole contracts", () => {
    // FII on 20 Nov 2026 (real file): short index futures, long stock futures
    expect(nets(P())).toEqual({
      indexFutures: -70454,
      stockFutures: 1001620,
      indexOptions: 882959 - 992566 + (1041238 - 1425970),
    });
  });

  it("gives the long share of index futures, or nothing when there are none", () => {
    expect(longShareBps(P())).toBe(3414); // 75,864 of 222,182 = 34.14%
    expect(longShareBps(P({ fut_idx_long: 0, fut_idx_short: 0 }))).toBeNull();
  });
});

describe("fetchReference", () => {
  it("reports a failed request", async () => {
    const f = (async () => new Response("", { status: 500 })) as unknown as typeof fetch;
    await expect(fetchReference(5, f)).rejects.toThrow("HTTP 500");
  });
});
