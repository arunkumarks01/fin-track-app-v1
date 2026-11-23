import { parseTradePlan } from "./risk";

export type CandidateMode = "swing" | "intraday";
export type CandidateInstrument = "underlying" | "future" | "option";

type Json = Record<string, unknown>;

function record(value: unknown): Json {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Json;
  throw new Error("Invalid candidate response");
}

function text(value: unknown): string {
  if (typeof value === "string") return value;
  throw new Error("Invalid candidate text");
}

function number(value: unknown): number | null {
  if (value === null) return null;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  throw new Error("Invalid candidate value");
}

function list(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  throw new Error("Invalid candidate list");
}

function choice<T extends string>(value: unknown, choices: readonly T[]): T {
  const found = choices.find((item) => item === value);
  if (found !== undefined) return found;
  throw new Error("Invalid candidate status");
}

function when(value: unknown): string {
  const iso = text(value);
  if (Number.isFinite(Date.parse(iso))) return iso;
  throw new Error("Invalid candidate date");
}

function candidate(value: unknown) {
  const item = record(value);
  const conviction = record(item.conviction);
  const contract = item.contract == null ? null : record(item.contract);
  return {
    id: text(item.id),
    tradePlan: parseTradePlan(item.trade_plan),
    name: text(item.name),
    status: choice(item.status, ["matched", "no_match", "unavailable", "disabled"] as const),
    direction: choice(item.direction, ["long", "short"] as const),
    reason: item.reason == null ? null : text(item.reason),
    invalidation: item.invalidation === null ? null : text(item.invalidation),
    contract: contract && {
      type: choice(contract.type, ["CE", "PE", "FUT"] as const),
      expiry: text(contract.expiry),
      strike: number(contract.strike_paisa ?? null),
      bid: number(contract.bid_paisa ?? null),
      ask: number(contract.ask_paisa ?? null),
      close: number(contract.close_paisa ?? null),
      oi: number(contract.oi ?? null),
      iv: number(contract.iv_pct ?? null),
      volume: number(contract.volume ?? null),
      snapshotAt: contract.snapshot_at ? when(contract.snapshot_at) : null,
      tradeDate: contract.trade_date ? text(contract.trade_date) : null,
    },
    conviction: {
      probability: number(conviction.win_probability_pct),
      observations: number(conviction.observations),
      minimum: number(conviction.minimum_observations),
      evaluatedThrough: conviction.evaluated_through ? when(conviction.evaluated_through) : null,
    },
    evidence: list(item.evidence).map((value) => {
      const row = record(value);
      return {
        label: text(row.label),
        field: text(row.field),
        comparator: text(row.comparison),
        observed: number(row.observed),
        threshold: number(row.threshold),
        thresholdField: row.threshold_field === null ? null : text(row.threshold_field),
        result: choice(row.result, ["pass", "fail", "unavailable"] as const),
        previous: row.previous === true,
      };
    }),
  };
}

export type Candidate = ReturnType<typeof candidate>;

export function parseCandidates(body: unknown) {
  const result = record(body);
  const news = record(result.news);
  const fundamentals = result.fundamentals === null ? null : record(result.fundamentals);
  return {
    symbol: text(result.symbol),
    mode: choice(result.mode, ["swing", "intraday"] as const),
    interval: text(result.interval),
    source: text(result.source),
    asOf: when(result.as_of),
    latestBar: number(result.latest_bar),
    previousBar: number(result.previous_bar),
    benchmarkSession:
      result.latest_benchmark_session === null ? null : text(result.latest_benchmark_session),
    dataStatus: text(result.data_status),
    news: {
      status: choice(news.status, ["classified", "unknown"] as const),
      message: news.status === "classified" ? text(news.sentiment) : text(news.reason),
      material: news.material_event === true,
      sources:
        news.status === "classified"
          ? list(news.sources).map((value) => {
              const item = record(value);
              const link = text(item.link);
              const url = new URL(link);
              if (url.protocol !== "https:" && url.protocol !== "http:")
                throw new Error("Invalid news link");
              return { title: text(item.title), link };
            })
          : [],
    },
    fundamentals: fundamentals && {
      marketCap: number(fundamentals.market_cap_paisa),
      issueSize: number(fundamentals.issue_size),
      tradeDate: text(fundamentals.trade_date),
    },
    evaluations: list(result.evaluations).map(candidate),
  };
}

export type CandidateReport = ReturnType<typeof parseCandidates>;

export async function fetchCandidates(
  symbol: string,
  mode: CandidateMode,
  instrument: CandidateInstrument,
  signal?: AbortSignal,
) {
  const query = new URLSearchParams({ symbol, mode, instrument });
  const response = await fetch(`/api/scoring/candidates?${query}`, { signal, cache: "no-store" });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
    if (response.status === 404 && detail === "Not Found") {
      throw new Error("The running backend does not yet serve Phase 3 candidates.");
    }
    throw new Error(
      typeof detail === "string" ? detail : `Candidate request failed (${response.status}).`,
    );
  }
  return parseCandidates(body);
}
