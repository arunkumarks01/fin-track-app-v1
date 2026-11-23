/**
 * Chart data: the strict contract for /api/data/symbols + /api/data/candles, and the
 * OpenAlgo Charts DataFeed over it. FinTrack serves every interval itself (session-anchored
 * intraday, native daily, server-built weekly) — the chart never re-buckets — and declares
 * honestly which intervals have as-traded ("raw") prices. Prices arrive in integer paisa and
 * become rupees only here, at the display edge.
 */
import type {
  Bar,
  BarsPage,
  BarsPageRequest,
  BarsRequest,
  DataFeed,
  DataVariantCapabilities,
  DataVariantQuery,
  SymbolMatch,
  SymbolSearchRequest,
} from "openalgo-charts";

import { paisaToRupees } from "./format";

export class ChartContractError extends Error {}

type Rec = Record<string, unknown>;

function isRecord(v: unknown): v is Rec {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function fail(what: string): never {
  throw new ChartContractError(`chart contract: ${what}`);
}

function int(v: unknown, what: string): number {
  return typeof v === "number" && Number.isInteger(v) ? v : fail(what);
}

function str(v: unknown, what: string): string {
  return typeof v === "string" ? v : fail(what);
}

/** The intervals FinTrack serves, in the widget's codes. */
export const INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w"] as const;
/** Intervals with as-traded prices (NSE official daily/weekly, measured 1-minute). */
export const RAW_INTERVALS: readonly string[] = ["1m", "D", "W"];

export function apiChartInterval(interval: string): string {
  if (interval === "1d") return "D";
  if (interval === "1w") return "W";
  return interval;
}

export type ChartSymbol = { symbol: string; exchange: string; tradingsymbol: string; kind: string };

export function parseSymbols(body: unknown): ChartSymbol[] {
  if (!isRecord(body) || !Array.isArray(body.symbols)) fail("symbols");
  return body.symbols.map((x) => {
    const r = isRecord(x) ? x : fail("symbol row");
    const kind = str(r.kind, "kind");
    if (kind !== "equity" && kind !== "index") fail(`kind ${kind}`);
    return {
      symbol: str(r.symbol, "symbol"),
      exchange: str(r.exchange, "exchange"),
      tradingsymbol: str(r.tradingsymbol, "tradingsymbol"),
      kind,
    };
  });
}

export type CandlePage = {
  bars: Bar[];
  hasMore: boolean;
  nextBefore: number | null;
  source: string;
  omittedUnusable: number;
};

export async function fetchSymbols(): Promise<ChartSymbol[]> {
  const res = await fetch("/api/data/symbols", { cache: "no-store" });
  if (!res.ok) throw new Error(`symbols request failed: HTTP ${res.status}`);
  return parseSymbols(await res.json());
}

export function parseCandles(body: unknown): CandlePage {
  if (!isRecord(body) || !Array.isArray(body.bars)) fail("candles");
  if (body.prices !== "integer paisa") fail("price unit");
  if (typeof body.has_more !== "boolean") fail("has_more");
  const bars = body.bars.map((x): Bar => {
    const b = isRecord(x) ? x : fail("bar");
    const o = int(b.o, "o");
    const h = int(b.h, "h");
    const l = int(b.l, "l");
    const c = int(b.c, "c");
    if (h < Math.max(o, c, l) || l > Math.min(o, c)) fail("bar OHLC order");
    return {
      time: int(b.t, "t"),
      open: paisaToRupees(o),
      high: paisaToRupees(h),
      low: paisaToRupees(l),
      close: paisaToRupees(c),
      volume: int(b.v, "v"),
    };
  });
  bars.reduce((prev, b) => {
    if (b.time <= prev) fail("bars not strictly ascending");
    return b.time;
  }, -Infinity);
  return {
    bars,
    hasMore: body.has_more,
    nextBefore: body.next_before === null ? null : int(body.next_before, "next_before"),
    source: str(body.source, "source"),
    omittedUnusable: int(body.omitted_unusable, "omitted_unusable"),
  };
}

type Fetcher = typeof fetch;

/** OpenAlgo Charts DataFeed backed by FinTrack's read-only chart API. */
export class FinTrackFeed implements DataFeed {
  private universe: Promise<ChartSymbol[]> | null = null;
  /** The last page's provenance, for the page header (source + omitted bars). */
  lastPage: CandlePage | null = null;

  constructor(
    private readonly fetcher: Fetcher = (...a) => fetch(...a),
    private readonly maxBars = 5000,
    private readonly onPage?: (page: CandlePage, req: BarsRequest) => void,
  ) {}

  private symbols(): Promise<ChartSymbol[]> {
    this.universe ??= (async () => {
      const res = await this.fetcher("/api/data/symbols", { cache: "no-store" });
      if (!res.ok) {
        this.universe = null;
        throw new Error(`symbols failed: HTTP ${res.status}`);
      }
      return parseSymbols(await res.json());
    })();
    return this.universe;
  }

  private async page(req: BarsRequest, before?: number): Promise<CandlePage> {
    const params = new URLSearchParams({
      symbol: `${req.exchange}:${req.symbol}`,
      interval: apiChartInterval(req.interval),
      adjustment: req.variant?.adjustment ?? "adjusted",
      count: String(Math.min(Math.max(req.countBack ?? 1000, 1), this.maxBars)),
    });
    if (before !== undefined) params.set("before", String(before));
    const res = await this.fetcher(`/api/data/candles?${params}`, {
      cache: "no-store",
      signal: req.signal,
    });
    if (!res.ok) {
      const detail = await res
        .json()
        .then((b: unknown) => (isRecord(b) && typeof b.detail === "string" ? b.detail : ""))
        .catch(() => "");
      throw new Error(`candles failed: HTTP ${res.status}${detail ? ` — ${detail}` : ""}`);
    }
    const page = parseCandles(await res.json());
    this.lastPage = page;
    this.onPage?.(page, req);
    return page;
  }

  async getBars(req: BarsRequest): Promise<Bar[]> {
    return (await this.page(req, req.to !== undefined ? req.to + 1 : undefined)).bars;
  }

  async getBarsPage(req: BarsPageRequest): Promise<BarsPage> {
    const page = await this.page(req, req.before);
    return {
      bars: page.bars,
      hasMore: page.hasMore,
      ...(page.nextBefore !== null ? { nextBefore: page.nextBefore } : {}),
    };
  }

  async dataVariants(query: DataVariantQuery): Promise<DataVariantCapabilities> {
    const sym = (await this.symbols()).find(
      (s) => s.tradingsymbol === query.symbol && s.exchange === query.exchange,
    );
    // indices are never adjusted (one series); derived intraday has no as-traded series
    if (!sym || sym.kind === "index" || !RAW_INTERVALS.includes(apiChartInterval(query.interval)))
      return {};
    return { adjustments: ["adjusted", "raw"] };
  }

  async searchSymbols(request: SymbolSearchRequest): Promise<SymbolMatch[]> {
    const q = request.query.trim().toUpperCase();
    const all = await this.symbols();
    const hits = all.filter((s) => s.tradingsymbol.toUpperCase().includes(q));
    hits.sort(
      (a, b) =>
        Number(!a.tradingsymbol.toUpperCase().startsWith(q)) -
          Number(!b.tradingsymbol.toUpperCase().startsWith(q)) ||
        a.tradingsymbol.localeCompare(b.tradingsymbol),
    );
    return hits.slice(0, 30).map((s) => ({
      symbol: s.tradingsymbol,
      exchange: s.exchange,
      name: s.tradingsymbol,
      assetClass: s.kind,
    }));
  }
}
