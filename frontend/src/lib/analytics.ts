/** Strict client for the read-only analytics reports. Null values remain unavailable. */

type RecordValue = Record<string, unknown>;

export class AnalyticsRequestError extends Error {
  constructor(
    message: string,
    readonly endpointUnavailable = false,
  ) {
    super(message);
  }
}

function fail(field: string): never {
  throw new AnalyticsRequestError(`Unexpected analytics response (${field}).`);
}

function record(value: unknown, field: string): RecordValue {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(field);
  return value as RecordValue;
}

function array(value: unknown, field: string): unknown[] {
  return Array.isArray(value) ? value : fail(field);
}

function text(value: unknown, field: string): string {
  return typeof value === "string" && value.length > 0 ? value : fail(field);
}

function number(value: unknown, field: string): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fail(field);
}

function integer(value: unknown, field: string): number {
  const n = number(value, field);
  return Number.isSafeInteger(n) ? n : fail(field);
}

function natural(value: unknown, field: string): number {
  const n = integer(value, field);
  return n >= 0 ? n : fail(field);
}

function bool(value: unknown, field: string): boolean {
  return typeof value === "boolean" ? value : fail(field);
}

function optional<T>(value: unknown, field: string, parse: (v: unknown, k: string) => T): T | null {
  return value === null ? null : parse(value, field);
}

function day(value: unknown, field: string): string {
  const iso = text(value, field);
  return /^\d{4}-\d{2}-\d{2}$/.test(iso) && Number.isFinite(Date.parse(iso)) ? iso : fail(field);
}

function instant(value: unknown, field: string): string {
  const iso = text(value, field);
  return /(?:Z|[+-]\d{2}:\d{2})$/.test(iso) && Number.isFinite(Date.parse(iso)) ? iso : fail(field);
}

export type IndicatorRow = {
  t: number;
  status: string;
  usableBars: number;
  values: Record<string, number | null> | null;
};

export function parseIndicatorReport(body: unknown) {
  const report = record(body, "indicator report");
  if (report.price_basis !== "currently_stored_adjusted_history") fail("price basis");
  let prev = -Infinity;
  const rows: IndicatorRow[] = array(report.rows, "rows").map((item) => {
    const row = record(item, "indicator row");
    const t = integer(row.t, "candle timestamp");
    if (t <= prev || !Number.isFinite(new Date(t * 1000).getTime())) fail("candle order");
    prev = t;
    const values =
      row.values === null
        ? null
        : Object.fromEntries(
            Object.entries(record(row.values, "indicator values")).map(([k, v]) => [
              k,
              optional(v, k, k.endsWith("_paisa") ? integer : number),
            ]),
          );
    return {
      t,
      status: text(row.status, "row status"),
      usableBars: natural(row.usable_bars, "usable bars"),
      values,
    };
  });
  return {
    symbol: text(report.symbol, "symbol"),
    interval: text(report.interval, "interval"),
    status: text(report.status, "status"),
    benchmark: optional(report.benchmark, "benchmark", text),
    latestBenchmarkSession: optional(report.latest_benchmark_session, "benchmark session", day),
    behindBenchmark: optional(report.behind_benchmark, "behind benchmark", bool),
    rows,
  };
}

function contract(value: unknown) {
  const row = record(value, "option contract");
  const type = row.type;
  if (type !== "CE" && type !== "PE") fail("option type");
  const raw = optional(row.greeks, "greeks", record);
  return {
    strike: natural(row.strike_paisa, "strike"),
    type: type as "CE" | "PE",
    midpointTwice: optional(row.midpoint_twice_paisa, "midpoint", natural),
    bid: optional(row.bid_paisa, "bid", natural),
    ask: optional(row.ask_paisa, "ask", natural),
    oi: optional(row.oi, "open interest", natural),
    volume: optional(row.volume, "volume", natural),
    oiChange: optional(row.oi_change, "open interest change", integer),
    pattern: optional(row.oi_premium_pattern, "open interest pattern", text),
    iv: optional(row.iv_pct, "implied volatility", number),
    reason: optional(row.unavailable_reason, "model status", text),
    greeks: raw && {
      delta: number(raw.delta, "delta"),
      gamma: number(raw.gamma_per_paisa, "gamma"),
      theta: number(raw.theta_paisa_per_day, "theta"),
      vega: number(raw.vega_paisa_per_vol_point, "vega"),
      rho: number(raw.rho_paisa_per_rate_point, "rho"),
    },
  };
}

function strikes(value: unknown, field: string): number[] {
  return array(value, field).map((strike) => natural(strike, field));
}

function expiry(value: unknown) {
  const row = record(value, "expiry");
  const wall = optional(row.gamma_wall, "gamma wall", record);
  const contracts = array(row.contracts, "contracts").map(contract);
  const keys = contracts.map((row) => `${row.strike}-${row.type}`);
  if (new Set(keys).size !== keys.length) fail("duplicate option contract");
  return {
    date: day(row.expiry, "expiry date"),
    days: number(row.days_to_expiry, "days to expiry"),
    pcrOI: optional(row.pcr_oi, "OI put/call ratio", number),
    pcrVolume: optional(row.pcr_volume, "volume put/call ratio", number),
    maxPain: optional(row.max_pain_strikes_paisa, "max pain", strikes),
    gammaWall: wall && {
      strikes: strikes(wall.strike_paisa, "gamma wall strikes"),
      exposure: natural(wall.gross_gamma_exposure_paisa_per_1pct, "gamma exposure"),
    },
    atmIV: optional(row.atm_iv_pct, "ATM IV", number),
    complete: bool(row.complete_pairs, "complete strike pairs"),
    contracts,
  };
}

export type OptionExpiry = ReturnType<typeof expiry>;
export type OptionContract = ReturnType<typeof contract>;

export function parseOptionReport(body: unknown) {
  const report = record(body, "options report");
  const underlying = text(report.underlying, "underlying");
  if (report.status === "no_snapshot") {
    if (array(report.expiries, "expiries").length !== 0) fail("empty snapshot");
    return { status: "no_snapshot" as const, underlying };
  }
  const status = report.status;
  if (status !== "ok" && status !== "partial" && status !== "error") fail("snapshot status");
  if (report.coverage !== "captured_strikes_only") fail("snapshot coverage");
  const rank = record(report.iv_rank, "IV rank");
  const expiries = array(report.expiries, "expiries").map(expiry);
  if (new Set(expiries.map((row) => row.date)).size !== expiries.length) fail("duplicate expiry");
  return {
    status,
    underlying,
    asOf: instant(report.snapshot_at, "snapshot time"),
    stale: bool(report.stale, "stale"),
    spot: optional(report.spot_paisa, "spot", natural),
    future: optional(report.near_future_paisa, "near future", natural),
    futureExpiry: optional(report.near_future_expiry, "future expiry", day),
    previousAsOf: optional(report.oi_comparison_snapshot_at, "comparison time", instant),
    rateBps: optional(report.rate_bps, "rate", integer),
    dividendBps: optional(report.dividend_bps, "dividend", natural),
    rank: {
      days: natural(rank.tenor_days, "IV tenor"),
      current: optional(rank.current_iv_pct, "tenor IV", number),
      pct: optional(rank.rank_pct, "IV rank", number),
      observations: natural(rank.observations, "observations"),
      minimum: natural(rank.minimum_observations, "minimum observations"),
    },
    expiries,
  } as const;
}

async function get(path: string, signal: AbortSignal, fetcher: typeof fetch) {
  const response = await fetcher(path, { signal, cache: "no-store" });
  const body: unknown = await response.json().catch(() => null);
  if (response.ok) return body;
  const detail =
    body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
      ? body.detail
      : null;
  if (response.status === 404 && detail === "Not Found") {
    throw new AnalyticsRequestError(
      "The running backend does not serve Phase 2 analytics yet.",
      true,
    );
  }
  throw new AnalyticsRequestError(detail ?? `Analytics request failed (HTTP ${response.status}).`);
}

export async function fetchIndicators(
  symbol: string,
  interval: string,
  signal: AbortSignal,
  fetcher: typeof fetch = fetch,
) {
  const query = new URLSearchParams({ symbol, interval, count: "90" });
  const report = parseIndicatorReport(
    await get(`/api/analytics/indicators?${query}`, signal, fetcher),
  );
  return report.symbol === symbol && report.interval === interval
    ? report
    : fail("indicator selection");
}

export type PricingInputs = { rateBps?: number; dividendBps?: number };

export async function fetchOptions(
  underlying: string,
  pricing: PricingInputs,
  signal: AbortSignal,
  fetcher: typeof fetch = fetch,
) {
  const query = new URLSearchParams({ underlying });
  if (pricing.rateBps !== undefined) query.set("rate_bps", String(pricing.rateBps));
  if (pricing.dividendBps !== undefined) query.set("dividend_bps", String(pricing.dividendBps));
  const report = parseOptionReport(await get(`/api/analytics/options?${query}`, signal, fetcher));
  return report.underlying === underlying ? report : fail("underlying selection");
}

/** A whole basis point is one hundredth of one percent. Empty uses the server setting. */
export function basisPoints(value: string, min: number, max: number): number | undefined {
  const raw = value.trim();
  if (!raw) return undefined;
  if (!/^[+-]?\d+(?:\.\d{1,2})?$/.test(raw)) {
    throw new Error("Enter a percentage with up to two decimal places.");
  }
  const bps = Math.round(Number(raw) * 100);
  if (!Number.isSafeInteger(bps) || bps < min || bps > max) {
    throw new Error(`Enter a percentage from ${min / 100}% to ${max / 100}%.`);
  }
  return bps;
}

export function retryAnalytics(failure: number, error: Error) {
  return failure < 1 && !(error instanceof AnalyticsRequestError && error.endpointUnavailable);
}
