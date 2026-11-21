/**
 * Phase 1e contract (GET /api/data/reference): the load ledger per dataset and the latest
 * sessions of participant-wise F&O positions. Parsed strictly; counts are whole contracts.
 */

export class ReferenceContractError extends Error {}

function fail(what: string): never {
  throw new ReferenceContractError(`unexpected reference response: ${what}`);
}
function rec(v: unknown, what: string): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : fail(what);
}
function int(v: unknown, what: string): number {
  return Number.isInteger(v) && (v as number) >= 0 ? (v as number) : fail(what);
}
function isoDate(v: unknown, what: string): string {
  return typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : fail(what);
}
function optIsoDate(v: unknown, what: string): string | null {
  return v === null ? null : isoDate(v, what);
}

export const DATASETS = [
  "mto",
  "participant_oi",
  "participant_vol",
  "bulk_deals",
  "block_deals",
  "pr_text",
  "market_cap",
] as const;
export type Dataset = (typeof DATASETS)[number];
export const DATASET_LABEL: Record<Dataset, string> = {
  mto: "Delivery %",
  participant_oi: "F&O positions (open interest)",
  participant_vol: "F&O trading volume",
  bulk_deals: "Bulk deals",
  block_deals: "Block deals",
  pr_text: "Announcements and board meetings",
  market_cap: "Equity market cap",
};

export const PARTICIPANTS = ["FII", "DII", "Pro", "Client"] as const;
export type Participant = (typeof PARTICIPANTS)[number];
export const PARTICIPANT_LABEL: Record<Participant, string> = {
  FII: "FII (foreign institutions)",
  DII: "DII (domestic institutions)",
  Pro: "Pro (brokers' own trading)",
  Client: "Client (everyone else)",
};

export const FIELDS = [
  "fut_idx_long",
  "fut_idx_short",
  "fut_stk_long",
  "fut_stk_short",
  "opt_idx_call_long",
  "opt_idx_put_long",
  "opt_idx_call_short",
  "opt_idx_put_short",
  "opt_stk_call_long",
  "opt_stk_put_long",
  "opt_stk_call_short",
  "opt_stk_put_short",
  "total_long",
  "total_short",
] as const;
export type Field = (typeof FIELDS)[number];
export type Positions = Record<Field, number>;

export type LedgerRow = {
  ok: number;
  missing: number;
  error: number;
  oldest: string | null;
  newest: string | null;
  rejected: number;
  roundingDays: number;
};

export type ParticipantDay = {
  date: string;
  oi: Partial<Record<Participant, Positions>>;
  volume: Partial<Record<Participant, Positions>>;
};

function parseIndexHistory(v: unknown, name: string) {
  const row = rec(v, name);
  const minuteAt = row.minute_at;
  if (
    minuteAt !== null &&
    (typeof minuteAt !== "string" || !Number.isFinite(Date.parse(minuteAt)))
  ) {
    fail(`${name}.minute_at`);
  }
  return {
    dailyBars: int(row.daily_bars, `${name}.daily_bars`),
    dailyDate: optIsoDate(row.daily_date, `${name}.daily_date`),
    minuteBars: int(row.minute_bars, `${name}.minute_bars`),
    minuteAt,
  };
}

function parseSources(v: unknown) {
  const sources = rec(v, "sources");
  if (typeof sources.fred_key_configured !== "boolean") fail("fred_key_configured");
  return {
    newsItems: int(sources.news_items, "news_items"),
    newsErrors: int(sources.news_errors, "news_errors"),
    newsRefusals: int(sources.news_refusals, "news_refusals"),
    calendarUpcoming: int(sources.calendar_upcoming, "calendar_upcoming"),
    fredSeries: int(sources.fred_series, "fred_series"),
    fredErrors: int(sources.fred_errors, "fred_errors"),
    fredKeyConfigured: sources.fred_key_configured,
    giftNifty: parseIndexHistory(sources.gift_nifty, "gift_nifty"),
    indiaVix: parseIndexHistory(sources.india_vix, "india_vix"),
  };
}

export type Reference = {
  ledger: Record<Dataset, LedgerRow>;
  participants: ParticipantDay[];
  sources: ReturnType<typeof parseSources>;
};

function positions(v: unknown, what: string): Positions {
  const r = rec(v, what);
  return Object.fromEntries(FIELDS.map((f) => [f, int(r[f], `${what}.${f}`)])) as Positions;
}

function side(v: unknown, what: string): Partial<Record<Participant, Positions>> {
  const r = rec(v, what);
  const out: Partial<Record<Participant, Positions>> = {};
  for (const [k, p] of Object.entries(r)) {
    if (!(PARTICIPANTS as readonly string[]).includes(k)) fail(`${what}: participant ${k}`);
    out[k as Participant] = positions(p, `${what}.${k}`);
  }
  return out;
}

export function parseReference(body: unknown): Reference {
  const b = rec(body, "body");
  const l = rec(b.ledger, "ledger");
  const ledger = Object.fromEntries(
    DATASETS.map((ds) => {
      const r = rec(l[ds], `ledger.${ds}`);
      return [
        ds,
        {
          ok: int(r.ok, "ok"),
          missing: int(r.missing, "missing"),
          error: int(r.error, "error"),
          oldest: optIsoDate(r.oldest, "oldest"),
          newest: optIsoDate(r.newest, "newest"),
          rejected: int(r.rejected, "rejected"),
          roundingDays: int(r.rounding_days, "rounding_days"),
        },
      ];
    }),
  ) as Record<Dataset, LedgerRow>;
  if (!Array.isArray(b.participants)) fail("participants");
  const participants = b.participants.map((x, i) => {
    const d = rec(x, `participants[${i}]`);
    return {
      date: isoDate(d.date, "date"),
      oi: side(d.oi, "oi"),
      volume: side(d.volume, "volume"),
    };
  });
  return { ledger, participants, sources: parseSources(b.sources) };
}

export async function fetchReference(days: number, f: typeof fetch = fetch): Promise<Reference> {
  const res = await f(`/api/data/reference?days=${days}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`reference request failed: HTTP ${res.status}`);
  return parseReference(await res.json());
}

/** Net contracts (long − short) for index futures, stock futures, and index options
 * (calls long − short + puts short − long: a bullish-minus-bearish count). Whole contracts. */
export function nets(p: Positions) {
  return {
    indexFutures: p.fut_idx_long - p.fut_idx_short,
    stockFutures: p.fut_stk_long - p.fut_stk_short,
    indexOptions:
      p.opt_idx_call_long - p.opt_idx_call_short + (p.opt_idx_put_short - p.opt_idx_put_long),
  };
}

/** Share of index-futures positions that are long, in basis points (0..10000), half up. */
export function longShareBps(p: Positions): number | null {
  const total = p.fut_idx_long + p.fut_idx_short;
  return total === 0 ? null : Math.floor((p.fut_idx_long * 20_000 + total) / (total * 2));
}
