import { describe, expect, it } from "vitest";

import { describeAction, fetchStock, parseStock, StockContractError } from "./stock";

const BODY = {
  symbol: "NSE:AAA",
  exchange: "NSE",
  tradingsymbol: "AAA",
  kind: "equity",
  active: true,
  name: "AAA LTD",
  tick_size_paisa: 5,
  futures: { lot_size: 500, expiry: "2026-11-26" },
  names: [{ old: "OLDAAA", new: "AAA", date: "2024-05-01", company: "Aaa Limited" }],
  daily: {
    coverage: {
      kind: "equity",
      clean_bars: 2941,
      oldest: "2015-01-01",
      newest: "2026-11-19",
      duplicate_dates: 0,
      quarantined: 0,
      quarantined_total: 1,
      flagged: 0,
      weekday_gap_pct: 5.16,
      stale_weekdays: 0,
      sufficient: true,
      fresh: true,
      insufficient_reason: null,
      listing: "2015-01-01",
      pre_listing_bars: 0,
      suspended_weekdays: 0,
    },
    quarantined: { superseded_provisional: 1 },
    flagged: {},
  },
  minute: {
    analysis_start: "2016-01-01",
    coverage: {
      first: "2015-02-02",
      last: "2026-11-20",
      sessions: 2924,
      bars: 1090291,
      complete: 2862,
      short: 1,
      missing_minutes: 1,
      excess: 0,
      unknown: 0,
      missing_sessions: 1,
      market_short: 61,
      not_traded_sessions: 0,
      complete_pct: 99.97,
    },
    quarantined: { non_canonical_ts: 46 },
  },
  actions: [
    {
      ex_date: "2026-10-29",
      record_date: "2026-10-29",
      purpose: "INTDIV - RS 6.50 PER SH",
      kinds: ["dividend"],
      series: ["BE", "EQ"],
      split_from_paisa: null,
      split_to_paisa: null,
      bonus_new: null,
      bonus_held: null,
      dividend_paisa: 650,
      kite: "no_change",
      detail: null,
    },
  ],
  other_security_actions: 312,
  unlisted_steps: [
    {
      date: "2025-01-10",
      ratio: 0.2,
      class: "corroborated",
      magnitude: null,
      small: false,
      detail: "not in NSE's action list…",
    },
  ],
  as_traded_days: { measured: 2939, carried: 1 },
  market_cap: {
    date: "2026-11-20",
    series: "EQ",
    issue_size: "13532538722",
    market_cap_paisa: "1864107208955500",
  },
  events: {
    deals: [
      {
        date: "2026-11-20",
        kind: "bulk",
        client: "Example Fund",
        side: "BUY",
        quantity: 500000,
        price_paisa: 129016,
        price_exact: false,
        remarks: null,
      },
    ],
    announcements: [{ date: "2026-11-20", category: null, body: "Results declared" }],
    board_meetings: [
      {
        date: "2027-01-15",
        purpose: "Financial results",
        is_results: true,
        first_seen: "2026-11-20",
      },
    ],
  },
  delivery: {
    days_stored: 2941,
    oldest: "2015-01-01",
    newest: "2026-11-20",
    window: 1,
    avg_bps: 5253,
    recent: [
      {
        date: "2026-11-20",
        series: "EQ",
        traded_qty: 4785257,
        deliverable_qty: 2513771,
        deliv_bps: 5253,
      },
    ],
  },
};

describe("parseStock", () => {
  it("maps a full response", () => {
    const s = parseStock(BODY);
    expect(s).toMatchObject({
      symbol: "NSE:AAA",
      kind: "equity",
      futures: { lotSize: 500, expiry: "2026-11-26" },
      otherSecurityActions: 312,
      asTradedDays: { measured: 2939, carried: 1 },
    });
    expect(s.daily.coverage?.cleanBars).toBe(2941);
    expect(s.minute.coverage?.completePct).toBe(99.97);
    expect(s.actions[0]).toMatchObject({ kite: "no_change", dividendPaisa: 650 });
    expect(s.unlistedSteps[0]).toMatchObject({ cls: "corroborated", magnitude: null });
    expect(s.delivery).toMatchObject({ daysStored: 2941, avgBps: 5253 });
    expect(s.delivery.recent[0]?.deliverableQty).toBe(2513771);
    expect(s.marketCap?.paisa).toBe(1864107208955500n);
    expect(s.events.deals[0]).toMatchObject({ pricePaisa: 129016, priceExact: false });
    expect(s.events.boardMeetings[0]).toMatchObject({ isResults: true, firstSeen: "2026-11-20" });
  });

  it("accepts absent coverage and no futures (an index)", () => {
    const s = parseStock({
      ...BODY,
      kind: "index",
      futures: null,
      tick_size_paisa: null,
      market_cap: null,
      minute: { ...BODY.minute, coverage: null },
      daily: { ...BODY.daily, coverage: null },
    });
    expect(s.futures).toBeNull();
    expect(s.marketCap).toBeNull();
    expect(s.minute.coverage).toBeNull();
    expect(s.daily.coverage).toBeNull();
  });

  it("rejects a delivery percentage outside 0-100%", () => {
    const recent = [{ ...BODY.delivery.recent[0], deliv_bps: 10_001 }];
    expect(() => parseStock({ ...BODY, delivery: { ...BODY.delivery, recent } })).toThrow(
      StockContractError,
    );
  });

  it("rejects an unknown Kite status instead of guessing", () => {
    const actions = [{ ...BODY.actions[0], kite: "probably" }];
    expect(() => parseStock({ ...BODY, actions })).toThrow(StockContractError);
  });

  it("rejects non-integer paisa and missing fields", () => {
    const actions = [{ ...BODY.actions[0], dividend_paisa: 6.5 }];
    expect(() => parseStock({ ...BODY, actions })).toThrow(StockContractError);
    const without: Record<string, unknown> = { ...BODY };
    delete without.other_security_actions;
    expect(() => parseStock(without)).toThrow(StockContractError);
    expect(() => parseStock({ ...BODY, kind: "etf" })).toThrow(StockContractError);
  });
});

describe("describeAction", () => {
  const base = parseStock(BODY).actions[0]!;
  it("says each action in plain words", () => {
    expect(describeAction(base)).toBe("Dividend ₹6.50 per share");
    expect(describeAction({ ...base, kinds: ["dividend"], dividendPaisa: null })).toBe(
      "Dividend (amount not stated)",
    );
    expect(describeAction({ ...base, kinds: ["bonus"], bonusNew: 1, bonusHeld: 2 })).toBe(
      "Bonus: 1 new for every 2 held",
    );
    expect(
      describeAction({ ...base, kinds: ["split"], splitFromPaisa: 1000, splitToPaisa: 200 }),
    ).toBe("Split: face value ₹10.00 → ₹2.00");
    expect(describeAction({ ...base, kinds: ["other"] })).toBe("Other — see NSE's text below");
  });
});

describe("fetchStock", () => {
  it("encodes the symbol and reports an unknown stock plainly", async () => {
    let url = "";
    const notFound = (async (u: string) => {
      url = u;
      return new Response("{}", { status: 404 });
    }) as unknown as typeof fetch;
    await expect(fetchStock("NSE:NIFTY 50", notFound)).rejects.toThrow("not in the universe");
    expect(url).toBe("/api/data/stock?symbol=NSE%3ANIFTY%2050");
  });
});
