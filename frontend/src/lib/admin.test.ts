import { describe, expect, it } from "vitest";

import {
  AdminContractError,
  fetchOverview,
  parseIntradayCoverage,
  parseKiteStatus,
  parseLoginUrl,
  parseOverview,
} from "./admin";

const OVERVIEW = {
  generated_at: "2026-11-20T03:00:00+00:00",
  history_since: "2026-11-20T02:00:00+00:00",
  job_running: true,
  scheduler: {
    running: true,
    jobs: [
      {
        id: "daily_ingest",
        next_run_at: "2026-11-20T16:15:00+05:30",
        trigger: "cron[day_of_week='1-5', hour='16', minute='15']",
      },
    ],
  },
  runs: [
    {
      name: "intraday_ingest",
      status: "running",
      started_at: "2026-11-20T02:30:00+00:00",
      finished_at: null,
      result: { progress: { inserted: 10, quarantined: 1, errors: 0 } },
      error: null,
    },
    {
      name: "daily_ingest",
      status: "failed",
      started_at: "2026-11-20T02:10:00+00:00",
      finished_at: "2026-11-20T02:11:00+00:00",
      result: { backfill: { inserted: 224, quarantined: 0, errors: 2 } },
      error: "Kite session unavailable",
    },
    {
      name: "daily_ingest",
      status: "failed",
      started_at: "2026-11-20T02:05:00+00:00",
      finished_at: "2026-11-20T02:05:01+00:00",
      result: {},
      error: "no valid Kite session",
    },
  ],
  quarantine: [{ timeframe: "1m", reason: "non_canonical_ts", count: 39703 }],
  flags: [{ timeframe: "1d", flag: "large_gap", count: 12 }],
  cursor_errors: [
    {
      grain: "1m",
      symbol: "NSE:AAA",
      error: "timeout",
      updated_at: "2026-11-20T02:00:00+00:00",
    },
  ],
};

describe("parseOverview", () => {
  it("maps jobs, runs (running, finished, failed-early), counts and cursor errors", () => {
    const o = parseOverview(OVERVIEW);
    expect(o.jobRunning).toBe(true);
    expect(o.scheduler?.jobs[0]).toEqual({
      id: "daily_ingest",
      nextRunAt: "2026-11-20T16:15:00+05:30",
      trigger: "cron[day_of_week='1-5', hour='16', minute='15']",
    });
    expect(o.runs.map((r) => [r.status, r.inserted, r.quarantined, r.symbolErrors])).toEqual([
      ["running", 10, 1, 0],
      ["failed", 224, 0, 2],
      ["failed", null, null, null],
    ]);
    expect(o.quarantine).toEqual([{ timeframe: "1m", label: "non_canonical_ts", count: 39703 }]);
    expect(o.flags).toEqual([{ timeframe: "1d", label: "large_gap", count: 12 }]);
    expect(o.cursorErrors[0]).toMatchObject({ grain: "1m", symbol: "NSE:AAA", error: "timeout" });
  });

  it("accepts a missing scheduler as null (shown as unknown, not as running)", () => {
    expect(parseOverview({ ...OVERVIEW, scheduler: null }).scheduler).toBeNull();
  });

  it.each([
    ["no runs array", { ...OVERVIEW, runs: null }],
    ["unknown run status", { ...OVERVIEW, runs: [{ ...OVERVIEW.runs[0], status: "done" }] }],
    [
      "count as string",
      { ...OVERVIEW, quarantine: [{ timeframe: "1d", reason: "x", count: "3" }] },
    ],
    [
      "bad cursor grain",
      { ...OVERVIEW, cursor_errors: [{ ...OVERVIEW.cursor_errors[0], grain: "5m" }] },
    ],
    ["job_running missing", { ...OVERVIEW, job_running: undefined }],
    [
      "totals with a non-numeric count",
      { ...OVERVIEW, runs: [{ ...OVERVIEW.runs[1], result: { backfill: { inserted: "many" } } }] },
    ],
  ])("rejects %s", (_name, body) => {
    expect(() => parseOverview(body)).toThrow(AdminContractError);
  });

  it("fails on an HTTP error instead of showing stale or empty data", async () => {
    const fetcher = (async () => new Response("{}", { status: 503 })) as unknown as typeof fetch;
    await expect(fetchOverview(fetcher)).rejects.toThrow("HTTP 503");
  });
});

describe("parseKiteStatus", () => {
  it("maps a connected session", () => {
    expect(
      parseKiteStatus({
        configured: true,
        connected: true,
        status: "connected",
        user_id: "ZA9371",
        expires_at: "2026-11-21T06:00:00+05:30",
        hours_left: 21.5,
        message: "Session expires in 21h",
      }),
    ).toEqual({
      status: "connected",
      userId: "ZA9371",
      expiresAt: "2026-11-21T06:00:00+05:30",
      hoursLeft: 21.5,
      message: "Session expires in 21h",
    });
  });

  it("maps not-configured (no user, no expiry)", () => {
    expect(
      parseKiteStatus({ configured: false, connected: false, status: "not_configured" }),
    ).toMatchObject({ status: "not_configured", userId: null, expiresAt: null });
  });

  it.each([
    ["unknown status", { connected: false, status: "maybe" }],
    ["connected flag disagreeing with status", { connected: true, status: "expired" }],
    ["missing connected flag", { status: "connected" }],
  ])("rejects %s", (_name, body) => {
    expect(() => parseKiteStatus(body)).toThrow(AdminContractError);
  });
});

describe("parseLoginUrl", () => {
  it("accepts only a Kite login URL", () => {
    const ok = "https://kite.zerodha.com/connect/login?v=3&api_key=k&redirect_params=state%3Dx";
    expect(parseLoginUrl({ login_url: ok })).toBe(ok);
    expect(() => parseLoginUrl({ login_url: "https://evil.example/login" })).toThrow(
      AdminContractError,
    );
    expect(() => parseLoginUrl({})).toThrow(AdminContractError);
  });
});

const sym = (symbol: string, review: number) => ({
  symbol,
  first: "2015-01-09",
  last: "2026-11-19",
  sessions: 100,
  bars: 37500,
  complete: 100 - review,
  short: review,
  missing_minutes: review * 15,
  excess: 0,
  unknown: 0,
  missing_sessions: 0,
  not_traded_sessions: 0,
  complete_pct: 100 - review,
  review,
});

const INTRADAY = {
  generated_at: "2026-11-20T03:00:00+00:00",
  db_size_gb: 19.963,
  analysis_start: "2016-01-01",
  summary: {
    symbols: 3,
    minute_bars: 112500,
    sessions: 300,
    complete_sessions: 290,
    short_sessions: 10,
    missing_minutes: 150,
    excess_sessions: 0,
    unknown_sessions: 0,
    market_short_sessions: 4,
    missing_sessions: 0,
    not_traded_sessions: 72,
    oldest: "2015-01-09",
    newest: "2026-11-19",
  },
  symbols: [sym("NSE:BBB", 3), sym("NSE:AAA", 7), sym("NSE:CCC", 0)],
};

describe("parseIntradayCoverage", () => {
  it("maps the summary and lists only symbols needing review, worst first", () => {
    const c = parseIntradayCoverage(INTRADAY);
    expect(c).toMatchObject({
      symbols: 3,
      minuteBars: 112500,
      complete: 290,
      short: 10,
      marketShort: 4,
      notTradedSessions: 72,
      analysisStart: "2016-01-01",
    });
    expect(c.worst.map((s) => [s.symbol, s.review])).toEqual([
      ["NSE:AAA", 7],
      ["NSE:BBB", 3],
    ]);
  });

  it("rejects a missing or malformed analysis start", () => {
    const without: Record<string, unknown> = { ...INTRADAY };
    delete without.analysis_start;
    expect(() => parseIntradayCoverage(without)).toThrow(AdminContractError);
    expect(() => parseIntradayCoverage({ ...INTRADAY, analysis_start: null })).toThrow(
      AdminContractError,
    );
    expect(() => parseIntradayCoverage({ ...INTRADAY, analysis_start: "2016" })).toThrow(
      AdminContractError,
    );
  });

  it("rejects a summary with a missing count", () => {
    const summary: Record<string, unknown> = { ...INTRADAY.summary };
    delete summary.short_sessions;
    expect(() => parseIntradayCoverage({ ...INTRADAY, summary })).toThrow(AdminContractError);
  });
});
