import { describe, expect, it } from "vitest";

import { IngestContractError, fetchJobs, parseCoverage, parseJobs } from "./ingest";

const RUNNING = {
  busy: true,
  last: {
    name: "daily_ingest",
    status: "running",
    started_at: "2026-11-19T12:48:47+00:00",
    finished_at: null,
    result: {},
    error: null,
  },
};

const COVERAGE = {
  passed: false,
  as_of_session: "2026-11-19",
  failures: ["only 40.0% of symbols have ≥250 clean bars (need 95%)"],
  summary: {
    symbols: 5,
    sufficient: 2,
    sufficient_pct: 40.0,
    clean_bars: 1234,
    quarantined: 1,
    duplicate_dates: 0,
  },
  symbols: [{ clean_bars: 600 }, { clean_bars: 634 }, { clean_bars: 0 }, { clean_bars: 0 }, {}],
};

describe("parseJobs", () => {
  it("maps a running job with no result yet", () => {
    expect(parseJobs(RUNNING)).toEqual({
      busy: true,
      name: "daily_ingest",
      status: "running",
      startedAt: "2026-11-19T12:48:47+00:00",
      finishedAt: null,
      error: null,
      inserted: null,
      quarantined: null,
      symbolErrors: null,
      intraday: null,
    });
  });

  it("maps a finished job's backfill totals", () => {
    const done = {
      busy: false,
      last: {
        ...RUNNING.last,
        status: "ok",
        finished_at: "2026-11-19T12:55:00+00:00",
        result: { backfill: { inserted: 500_000, quarantined: 3, errors: 1 } },
      },
    };
    expect(parseJobs(done)).toMatchObject({ status: "ok", inserted: 500_000, symbolErrors: 1 });
  });

  it.each([
    null,
    { busy: "yes", last: RUNNING.last },
    { busy: true, last: { ...RUNNING.last, status: "paused" } },
    { busy: true, last: { ...RUNNING.last, result: { backfill: { inserted: "many" } } } },
  ])("rejects an unexpected shape: %o", (body) => {
    expect(() => parseJobs(body)).toThrow(IngestContractError);
  });
});

describe("parseJobs intraday", () => {
  it("reads live intraday progress while running", () => {
    const live = {
      busy: true,
      last: {
        ...RUNNING.last,
        name: "intraday_ingest",
        result: {
          progress: {
            rounds: 3,
            windows: 600,
            inserted: 12_000_000,
            already_stored: 0,
            quarantined: 0,
            errors: 0,
            db_size_gb: 3.2,
            budget_gb: 35,
            deepest_common_date: "2026-03-01",
          },
        },
      },
    };
    expect(parseJobs(live).intraday).toEqual({
      rounds: 3,
      windows: 600,
      inserted: 12_000_000,
      deepestCommonDate: "2026-03-01",
      dbSizeGb: 3.2,
      budgetGb: 35,
    });
  });

  it("ignores another job's live progress instead of failing", () => {
    const reference = {
      ...RUNNING,
      last: {
        ...RUNNING.last,
        name: "reference_ingest",
        result: { progress: { todo: { mto: 3 }, done: 1, ok: 1, inserted: 64 } },
      },
    };
    expect(parseJobs(reference)).toMatchObject({ name: "reference_ingest", intraday: null });
  });

  it("rejects a malformed progress block", () => {
    const bad = {
      busy: true,
      last: {
        ...RUNNING.last,
        name: "intraday_ingest",
        result: { progress: { rounds: "three", budget_gb: 35 } },
      },
    };
    expect(() => parseJobs(bad)).toThrow(IngestContractError);
  });
});

describe("parseCoverage", () => {
  it("counts symbols with data and keeps the gate verdict honest", () => {
    expect(parseCoverage(COVERAGE)).toEqual({
      passed: false,
      asOfSession: "2026-11-19",
      failures: COVERAGE.failures,
      symbols: 5,
      withData: 2,
      sufficient: 2,
      sufficientPct: 40,
      cleanBars: 1234,
      quarantined: 1,
      duplicateDates: 0,
    });
  });

  it.each([{}, { ...COVERAGE, summary: null }, { ...COVERAGE, passed: "no" }])(
    "rejects %o",
    (body) => {
      expect(() => parseCoverage(body)).toThrow(IngestContractError);
    },
  );
});

describe("fetchJobs", () => {
  it("fails on HTTP errors instead of guessing", async () => {
    const fetcher: typeof fetch = async () => new Response("{}", { status: 500 });
    await expect(fetchJobs(fetcher)).rejects.toThrow("HTTP 500");
  });
});
