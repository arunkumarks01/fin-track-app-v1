import { describe, expect, it, vi } from "vitest";

import {
  basisPoints,
  fetchIndicators,
  fetchOptions,
  parseIndicatorReport,
  parseOptionReport,
} from "./analytics";

const chartRow = {
  t: 1795141800,
  status: "warming_up",
  usable_bars: 51,
  values: { ema_20_paisa: 133350, ema_50_paisa: 132250, ema_200_paisa: null, rsi_pct: 48.125 },
};
const indicatorReport = {
  symbol: "NSE:RELIANCE",
  interval: "D",
  price_basis: "currently_stored_adjusted_history",
  status: "warming_up",
  benchmark: "NSE:NIFTY 50",
  latest_benchmark_session: "2026-11-20",
  behind_benchmark: false,
  rows: [chartRow],
};

const contract = {
  strike_paisa: 2500000,
  type: "CE",
  midpoint_twice_paisa: 113,
  bid_paisa: 56,
  ask_paisa: 57,
  oi: 180,
  volume: 42,
  oi_change: -6,
  oi_premium_pattern: "premium_up_oi_down",
  iv_pct: 19.7,
  unavailable_reason: null,
  greeks: {
    delta: 0.38,
    gamma_per_paisa: 0.000007,
    theta_paisa_per_day: -5.25,
    vega_paisa_per_vol_point: 8.2,
    rho_paisa_per_rate_point: 1.2,
  },
};
const expiry = {
  expiry: "2026-12-10",
  days_to_expiry: 20.0,
  pcr_oi: 1.1,
  pcr_volume: null,
  max_pain_strikes_paisa: [2500000, 2510000],
  gamma_wall: { strike_paisa: [2510000], gross_gamma_exposure_paisa_per_1pct: 10000000 },
  atm_iv_pct: 18.2,
  complete_pairs: true,
  contracts: [contract],
};
const optionReport = {
  underlying: "NIFTY",
  status: "ok",
  coverage: "captured_strikes_only",
  snapshot_at: "2026-11-20T15:00:00+05:30",
  stale: true,
  spot_paisa: 2500000,
  near_future_paisa: null,
  near_future_expiry: null,
  oi_comparison_snapshot_at: null,
  rate_bps: 625,
  dividend_bps: 0,
  iv_rank: {
    tenor_days: 30,
    current_iv_pct: 18.2,
    rank_pct: null,
    observations: 1,
    minimum_observations: 20,
  },
  expiries: [expiry],
};
const fakeFetch = (body: object, status = 200) =>
  vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(body), { status }));

describe("indicator reports", () => {
  it("keeps warm-up and zero separate and rejects invalid prices or timestamps", () => {
    expect(parseIndicatorReport(indicatorReport).rows[0]?.values?.ema_200_paisa).toBeNull();
    expect(
      parseIndicatorReport({ ...indicatorReport, rows: [{ ...chartRow, values: { rsi_pct: 0 } }] })
        .rows[0]?.values?.rsi_pct,
    ).toBe(0);
    expect(() => parseIndicatorReport({ ...indicatorReport, rows: [chartRow, chartRow] })).toThrow(
      /order/,
    );
    expect(() =>
      parseIndicatorReport({
        ...indicatorReport,
        rows: [{ ...chartRow, values: { ema_50_paisa: 1.25 } }],
      }),
    ).toThrow(/ema_50_paisa/);
  });

  it("requests the chart interval and reports an old running backend", async () => {
    const signal = new AbortController().signal;
    const old = fakeFetch({ detail: "Not Found" }, 404);
    await expect(fetchIndicators("NSE:RELIANCE", "D", signal, old)).rejects.toMatchObject({
      endpointUnavailable: true,
    });
    expect(old).toHaveBeenCalledWith(
      "/api/analytics/indicators?symbol=NSE%3ARELIANCE&interval=D&count=90",
      { signal, cache: "no-store" },
    );
  });
});

describe("option reports", () => {
  it("preserves stale and missing values, paired strikes, dates, and Greeks", () => {
    const row = parseOptionReport(optionReport);
    expect(row.status).toBe("ok");
    if (row.status === "no_snapshot") throw new Error("missing fixture");
    expect(row.stale).toBe(true);
    expect(row.rank.pct).toBeNull();
    expect(row.expiries[0]?.maxPain).toEqual([2500000, 2510000]);
    expect(row.expiries[0]?.contracts[0]?.greeks?.theta).toBe(-5.25);
    expect(() =>
      parseOptionReport({
        ...optionReport,
        expiries: [{ ...expiry, contracts: [contract, contract] }],
      }),
    ).toThrow(/duplicate/);
  });

  it("sends explicit zero and preserves an empty chain", async () => {
    const signal = new AbortController().signal;
    const fetcher = fakeFetch({ underlying: "NIFTY", status: "no_snapshot", expiries: [] });
    await expect(fetchOptions("NIFTY", { dividendBps: 0 }, signal, fetcher)).resolves.toEqual({
      underlying: "NIFTY",
      status: "no_snapshot",
    });
    expect(fetcher).toHaveBeenCalledWith("/api/analytics/options?underlying=NIFTY&dividend_bps=0", {
      signal,
      cache: "no-store",
    });
  });

  it("accepts only supported percentage precision and bounds", () => {
    expect(basisPoints(" ", 0, 5000)).toBeUndefined();
    expect(basisPoints("6.25", -2000, 5000)).toBe(625);
    expect(basisPoints("-0.01", -2000, 5000)).toBe(-1);
    expect(() => basisPoints("6.251", 0, 5000)).toThrow(/two decimal places/);
    expect(() => basisPoints("-1", 0, 5000)).toThrow(/0% to 50%/);
  });
});
