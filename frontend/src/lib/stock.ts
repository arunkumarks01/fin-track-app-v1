/**
 * The Stock page contract (GET /api/data/stock?symbol=…), parsed strictly: an unexpected
 * shape is an error, never a guessed value. Money stays integer paisa until display.
 */

import { formatRupees } from "./format";

export class StockContractError extends Error {}

function fail(what: string): never {
  throw new StockContractError(`unexpected stock response: ${what}`);
}

function rec(v: unknown, what: string): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : fail(what);
}

function arr(v: unknown, what: string): unknown[] {
  return Array.isArray(v) ? v : fail(what);
}

function str(v: unknown, what: string): string {
  return typeof v === "string" ? v : fail(what);
}

function optStr(v: unknown, what: string): string | null {
  return v === null ? null : str(v, what);
}

function num(v: unknown, what: string): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fail(what);
}

function optNum(v: unknown, what: string): number | null {
  return v === null ? null : num(v, what);
}

function int(v: unknown, what: string): number {
  return Number.isInteger(v) ? (v as number) : fail(what);
}

function optInt(v: unknown, what: string): number | null {
  return v === null ? null : int(v, what);
}

function bool(v: unknown, what: string): boolean {
  return typeof v === "boolean" ? v : fail(what);
}

function isoDate(v: unknown, what: string): string {
  const d = str(v, what);
  return /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : fail(what);
}

function optIsoDate(v: unknown, what: string): string | null {
  return v === null ? null : isoDate(v, what);
}

function counts(v: unknown, what: string): Record<string, number> {
  const r = rec(v, what);
  return Object.fromEntries(Object.entries(r).map(([k, n]) => [k, int(n, `${what}.${k}`)]));
}

/** How Kite's measured prices relate to one NSE action (see backend app/data/stock.py). */
export const KITE_STATUSES = [
  "matched",
  "cant_check",
  "mismatch",
  "no_change",
  "not_measured",
] as const;
export type KiteStatus = (typeof KITE_STATUSES)[number];

export type StockAction = {
  exDate: string;
  recordDate: string | null;
  purpose: string;
  kinds: string[];
  series: string[];
  splitFromPaisa: number | null;
  splitToPaisa: number | null;
  bonusNew: number | null;
  bonusHeld: number | null;
  dividendPaisa: number | null;
  kite: KiteStatus;
  detail: string | null;
};

export type UnlistedStep = {
  date: string;
  ratio: number;
  cls: string;
  magnitude: number | null;
  small: boolean;
  detail: string;
};

export type DailyHealth = {
  cleanBars: number;
  oldest: string | null;
  newest: string | null;
  duplicateDates: number;
  quarantinedUnresolved: number;
  weekdayGapPct: number | null;
  staleWeekdays: number | null;
  sufficient: boolean;
  fresh: boolean;
  insufficientReason: string | null;
  preListingBars: number;
  suspendedWeekdays: number;
};

export type MinuteHealth = {
  first: string;
  last: string;
  sessions: number;
  bars: number;
  complete: number;
  completePct: number;
  short: number;
  excess: number;
  missingSessions: number;
  notTradedSessions: number;
  marketShort: number;
  unknown: number;
};

export type Stock = {
  symbol: string;
  exchange: string;
  tradingsymbol: string;
  kind: "equity" | "index";
  active: boolean;
  name: string | null;
  tickSizePaisa: number | null;
  futures: { lotSize: number; expiry: string } | null;
  names: { old: string; new: string; date: string; company: string }[];
  daily: {
    coverage: DailyHealth | null;
    quarantined: Record<string, number>;
    flagged: Record<string, number>;
  };
  minute: {
    analysisStart: string;
    coverage: MinuteHealth | null;
    quarantined: Record<string, number>;
  };
  actions: StockAction[];
  otherSecurityActions: number;
  unlistedSteps: UnlistedStep[];
  asTradedDays: Record<string, number>;
};

function kind(v: unknown): "equity" | "index" {
  return v === "equity" || v === "index" ? v : fail("kind");
}

function kiteStatus(v: unknown): KiteStatus {
  return (KITE_STATUSES as readonly unknown[]).includes(v) ? (v as KiteStatus) : fail("kite");
}

function daily(v: unknown): DailyHealth | null {
  if (v === null) return null;
  const c = rec(v, "daily.coverage");
  return {
    cleanBars: int(c.clean_bars, "clean_bars"),
    oldest: optIsoDate(c.oldest, "oldest"),
    newest: optIsoDate(c.newest, "newest"),
    duplicateDates: int(c.duplicate_dates, "duplicate_dates"),
    quarantinedUnresolved: int(c.quarantined, "quarantined"),
    weekdayGapPct: optNum(c.weekday_gap_pct, "weekday_gap_pct"),
    staleWeekdays: optInt(c.stale_weekdays, "stale_weekdays"),
    sufficient: bool(c.sufficient, "sufficient"),
    fresh: bool(c.fresh, "fresh"),
    insufficientReason: optStr(c.insufficient_reason, "insufficient_reason"),
    preListingBars: int(c.pre_listing_bars, "pre_listing_bars"),
    suspendedWeekdays: int(c.suspended_weekdays, "suspended_weekdays"),
  };
}

function minute(v: unknown): MinuteHealth | null {
  if (v === null) return null;
  const c = rec(v, "minute.coverage");
  return {
    first: isoDate(c.first, "first"),
    last: isoDate(c.last, "last"),
    sessions: int(c.sessions, "sessions"),
    bars: int(c.bars, "bars"),
    complete: int(c.complete, "complete"),
    completePct: num(c.complete_pct, "complete_pct"),
    short: int(c.short, "short"),
    excess: int(c.excess, "excess"),
    missingSessions: int(c.missing_sessions, "missing_sessions"),
    notTradedSessions: int(c.not_traded_sessions, "not_traded_sessions"),
    marketShort: int(c.market_short, "market_short"),
    unknown: int(c.unknown, "unknown"),
  };
}

export function parseStock(body: unknown): Stock {
  const b = rec(body, "body");
  const fut = b.futures === null ? null : rec(b.futures, "futures");
  const d = rec(b.daily, "daily");
  const m = rec(b.minute, "minute");
  return {
    symbol: str(b.symbol, "symbol"),
    exchange: str(b.exchange, "exchange"),
    tradingsymbol: str(b.tradingsymbol, "tradingsymbol"),
    kind: kind(b.kind),
    active: bool(b.active, "active"),
    name: optStr(b.name, "name"),
    tickSizePaisa: optInt(b.tick_size_paisa, "tick_size_paisa"),
    futures: fut && {
      lotSize: int(fut.lot_size, "lot_size"),
      expiry: isoDate(fut.expiry, "expiry"),
    },
    names: arr(b.names, "names").map((x) => {
      const n = rec(x, "name");
      return {
        old: str(n.old, "old"),
        new: str(n.new, "new"),
        date: isoDate(n.date, "date"),
        company: str(n.company, "company"),
      };
    }),
    daily: {
      coverage: daily(d.coverage),
      quarantined: counts(d.quarantined, "daily.quarantined"),
      flagged: counts(d.flagged, "daily.flagged"),
    },
    minute: {
      analysisStart: isoDate(m.analysis_start, "analysis_start"),
      coverage: minute(m.coverage),
      quarantined: counts(m.quarantined, "minute.quarantined"),
    },
    actions: arr(b.actions, "actions").map((x) => {
      const a = rec(x, "action");
      return {
        exDate: isoDate(a.ex_date, "ex_date"),
        recordDate: optIsoDate(a.record_date, "record_date"),
        purpose: str(a.purpose, "purpose"),
        kinds: arr(a.kinds, "kinds").map((k) => str(k, "kinds[]")),
        series: arr(a.series, "series").map((k) => str(k, "series[]")),
        splitFromPaisa: optInt(a.split_from_paisa, "split_from_paisa"),
        splitToPaisa: optInt(a.split_to_paisa, "split_to_paisa"),
        bonusNew: optInt(a.bonus_new, "bonus_new"),
        bonusHeld: optInt(a.bonus_held, "bonus_held"),
        dividendPaisa: optInt(a.dividend_paisa, "dividend_paisa"),
        kite: kiteStatus(a.kite),
        detail: optStr(a.detail, "detail"),
      };
    }),
    otherSecurityActions: int(b.other_security_actions, "other_security_actions"),
    unlistedSteps: arr(b.unlisted_steps, "unlisted_steps").map((x) => {
      const s = rec(x, "step");
      return {
        date: isoDate(s.date, "date"),
        ratio: num(s.ratio, "ratio"),
        cls: str(s.class, "class"),
        magnitude: optNum(s.magnitude, "magnitude"),
        small: bool(s.small, "small"),
        detail: str(s.detail, "detail"),
      };
    }),
    asTradedDays: counts(b.as_traded_days, "as_traded_days"),
  };
}

export async function fetchStock(symbol: string, f: typeof fetch = fetch): Promise<Stock> {
  const res = await f(`/api/data/stock?symbol=${encodeURIComponent(symbol)}`, {
    cache: "no-store",
  });
  if (res.status === 404) throw new StockContractError(`${symbol} is not in the universe`);
  if (!res.ok) throw new Error(`stock request failed: HTTP ${res.status}`);
  return parseStock(await res.json());
}

/** One action in plain words, from the parsed fields (the raw NSE text is shown beside it). */
export function describeAction(a: StockAction): string {
  const parts: string[] = [];
  if (a.kinds.includes("split")) {
    parts.push(
      a.splitFromPaisa && a.splitToPaisa
        ? `Split: face value ${formatRupees(a.splitFromPaisa)} → ${formatRupees(a.splitToPaisa)}`
        : "Split (ratio not stated)",
    );
  }
  if (a.kinds.includes("bonus")) {
    parts.push(
      a.bonusNew && a.bonusHeld
        ? `Bonus: ${a.bonusNew} new for every ${a.bonusHeld} held`
        : "Bonus (ratio not stated)",
    );
  }
  if (a.kinds.includes("dividend")) {
    parts.push(
      a.dividendPaisa !== null
        ? `Dividend ${formatRupees(a.dividendPaisa)} per share`
        : "Dividend (amount not stated)",
    );
  }
  if (parts.length === 0) parts.push("Other — see NSE's text below");
  return parts.join(" + ");
}
