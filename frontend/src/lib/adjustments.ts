/**
 * Phase 1c contracts: /api/data/adjustments (measured Kite adjustment vs NSE official) and
 * /api/data/repairs (audited provisional-bar repairs). Parsed strictly: an unexpected shape is
 * an error, never a guessed value (honest surfaces).
 */
export class AdjustmentsContractError extends Error {}

type Rec = Record<string, unknown>;

function isRecord(v: unknown): v is Rec {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function fail(what: string): never {
  throw new AdjustmentsContractError(`adjustments contract: ${what}`);
}

function rec(v: unknown, what: string): Rec {
  return isRecord(v) ? v : fail(what);
}

function num(v: unknown, what: string): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fail(what);
}

function str(v: unknown, what: string): string {
  return typeof v === "string" ? v : fail(what);
}

function optStr(v: unknown, what: string): string | null {
  return v === null || v === undefined ? null : str(v, what);
}

function counts(v: unknown, what: string): Record<string, number> {
  const r = rec(v, what);
  return Object.fromEntries(Object.entries(r).map(([k, n]) => [k, num(n, `${what}.${k}`)]));
}

export type LedgerStatus = { dates: number; oldest: string | null; newest: string | null };

export type ReviewItem = {
  symbol: string;
  date: string;
  cls: string;
  /** measured size of an unexplained step (fraction); null = not sized */
  magnitude: number | null;
  detail: string;
};

export type AdjustmentsSummary = {
  lastFinalSession: string;
  archive: Partial<Record<"ok" | "missing" | "error", LedgerStatus>>;
  actions: number;
  actionsByKind: Record<string, number>;
  bhavRows: number;
  daysByQuality: Record<string, number>;
  eventsByClass: Record<string, number>;
  driftEvents: number;
  checksStale: number;
  review: ReviewItem[];
  /** unexplained steps below the review threshold: refit noise, counted not listed */
  reviewNoise: { count: number; below: number };
};

export function parseAdjustmentsSummary(body: unknown): AdjustmentsSummary {
  const b = rec(body, "summary");
  const ledger = rec(b.nse_archive, "nse_archive");
  const archive: AdjustmentsSummary["archive"] = {};
  for (const [k, v] of Object.entries(ledger)) {
    if (k !== "ok" && k !== "missing" && k !== "error") fail(`ledger status ${k}`);
    const l = rec(v, `ledger.${k}`);
    archive[k] = {
      dates: num(l.dates, "dates"),
      oldest: optStr(l.oldest, "oldest"),
      newest: optStr(l.newest, "newest"),
    };
  }
  const ca = rec(b.corporate_actions, "corporate_actions");
  if (!Array.isArray(b.review)) fail("review");
  const noise = rec(b.review_noise, "review_noise");
  if (typeof noise.below !== "number" || !Number.isFinite(noise.below)) fail("review_noise.below");
  return {
    lastFinalSession: str(b.last_final_session, "last_final_session"),
    archive,
    actions: num(ca.rows, "actions"),
    actionsByKind: counts(ca.by_kind, "by_kind"),
    bhavRows: num(b.nse_bhav_rows, "nse_bhav_rows"),
    daysByQuality: counts(b.days_by_quality, "days_by_quality"),
    eventsByClass: counts(b.events_by_class, "events_by_class"),
    driftEvents: num(b.drift_events, "drift_events"),
    checksStale: num(b.checks_stale, "checks_stale"),
    review: b.review.map((x) => {
      const r = rec(x, "review item");
      return {
        symbol: str(r.symbol, "symbol"),
        date: str(r.date, "date"),
        cls: str(r.class, "class"),
        magnitude:
          r.magnitude === null
            ? null
            : typeof r.magnitude === "number" && Number.isFinite(r.magnitude)
              ? r.magnitude
              : fail("magnitude"),
        detail: str(r.detail, "detail"),
      };
    }),
    reviewNoise: { count: num(noise.count, "review_noise.count"), below: noise.below },
  };
}

export type RepairsReport = {
  repairs: number;
  checked: number;
  matchOfficial: number;
  notCheckable: number;
  archiveRowsPurged: number;
  mismatches: { symbol: string; date: string; newClose: number; officialClose: number }[];
};

export function parseRepairs(body: unknown): RepairsReport {
  const b = rec(body, "repairs");
  if (!Array.isArray(b.mismatches)) fail("mismatches");
  return {
    repairs: num(b.repairs, "repairs"),
    checked: num(b.checked_against_official, "checked_against_official"),
    matchOfficial: num(b.match_official, "match_official"),
    notCheckable: num(b.not_checkable_yet, "not_checkable_yet"),
    archiveRowsPurged: num(b.archive_rows_purged, "archive_rows_purged"),
    mismatches: b.mismatches.map((x) => {
      const m = rec(x, "mismatch");
      return {
        symbol: str(m.symbol, "symbol"),
        date: str(m.date, "date"),
        newClose: num(m.new_close, "new_close"),
        officialClose: num(m.official_close, "official_close"),
      };
    }),
  };
}

async function getJson(path: string, fetcher: typeof fetch): Promise<unknown> {
  const res = await fetcher(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} failed: HTTP ${res.status}`);
  return res.json();
}

export const fetchAdjustmentsSummary = async (f: typeof fetch = fetch) =>
  parseAdjustmentsSummary(await getJson("/api/data/adjustments", f));
export const fetchRepairs = async (f: typeof fetch = fetch) =>
  parseRepairs(await getJson("/api/data/repairs", f));
