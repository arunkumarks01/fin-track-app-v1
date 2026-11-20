import { describe, expect, it } from "vitest";

import {
  AdjustmentsContractError,
  fetchRepairs,
  parseAdjustmentsSummary,
  parseRepairs,
} from "./adjustments";

const SUMMARY = {
  generated_at: "2026-11-20T04:00:00+00:00",
  last_final_session: "2026-11-19",
  nse_archive: {
    ok: { dates: 2950, oldest: "2015-01-01", newest: "2026-11-19" },
    missing: { dates: 2, oldest: "2016-03-07", newest: "2019-10-21" },
  },
  corporate_actions: { rows: 4200, by_kind: { dividend: 3500, split: 60, other: 700 } },
  nse_bhav_rows: 600000,
  days_by_quality: { measured: 580000, carried: 30, blip: 12, not_applicable: 40000 },
  events_by_class: { explained: 900, attributed: 400, unexplained_small: 25, suspect: 3 },
  drift_events: 0,
  checks_stale: 0,
  review: [{ symbol: "NSE:AAA", date: "2020-05-13", class: "suspect", detail: "price step x0.8" }],
};

describe("parseAdjustmentsSummary", () => {
  it("maps ledger, counts and the review list", () => {
    const s = parseAdjustmentsSummary(SUMMARY);
    expect(s.archive.ok?.dates).toBe(2950);
    expect(s.archive.missing?.newest).toBe("2019-10-21");
    expect(s.archive.error).toBeUndefined();
    expect(s.eventsByClass.suspect).toBe(3);
    expect(s.daysByQuality.not_applicable).toBe(40000);
    expect(s.review[0]).toEqual({
      symbol: "NSE:AAA",
      date: "2020-05-13",
      cls: "suspect",
      detail: "price step x0.8",
    });
  });

  it.each([
    ["unknown ledger status", { ...SUMMARY, nse_archive: { done: { dates: 1 } } }],
    ["count as string", { ...SUMMARY, events_by_class: { explained: "900" } }],
    ["missing review", { ...SUMMARY, review: undefined }],
    ["missing stale count", { ...SUMMARY, checks_stale: null }],
  ])("rejects %s", (_name, body) => {
    expect(() => parseAdjustmentsSummary(body)).toThrow(AdjustmentsContractError);
  });
});

describe("parseRepairs", () => {
  const REPAIRS = {
    repairs: 223,
    checked_against_official: 223,
    match_official: 222,
    not_checkable_yet: 0,
    mismatches: [
      {
        symbol: "NSE:AAA",
        date: "2026-11-19",
        old_close: 1,
        new_close: 2,
        official_close: 3,
        matches_official: false,
      },
    ],
    items: [],
  };

  it("maps the acceptance check", () => {
    expect(parseRepairs(REPAIRS)).toEqual({
      repairs: 223,
      checked: 223,
      matchOfficial: 222,
      notCheckable: 0,
      mismatches: [{ symbol: "NSE:AAA", date: "2026-11-19", newClose: 2, officialClose: 3 }],
    });
  });

  it("rejects a mismatch without an official close", () => {
    const bad = { ...REPAIRS, mismatches: [{ symbol: "X", date: "d", new_close: 2 }] };
    expect(() => parseRepairs(bad)).toThrow(AdjustmentsContractError);
  });

  it("fails on HTTP errors", async () => {
    const f = (async () => new Response("{}", { status: 503 })) as unknown as typeof fetch;
    await expect(fetchRepairs(f)).rejects.toThrow("HTTP 503");
  });
});
