/**
 * Admin/Health contracts: /api/admin/overview, /api/kite/status, /api/data/coverage/intraday.
 * Parsed strictly: an unexpected shape is an error, never a guessed value (honest surfaces).
 */
export class AdminContractError extends Error {}

type Rec = Record<string, unknown>;

function isRecord(v: unknown): v is Rec {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function fail(what: string): never {
  throw new AdminContractError(`admin contract: ${what}`);
}

function rec(v: unknown, what: string): Rec {
  return isRecord(v) ? v : fail(what);
}

function arr(v: unknown, what: string): unknown[] {
  return Array.isArray(v) ? v : fail(what);
}

function num(v: unknown, what: string): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fail(what);
}

function str(v: unknown, what: string): string {
  return typeof v === "string" ? v : fail(what);
}

function bool(v: unknown, what: string): boolean {
  return typeof v === "boolean" ? v : fail(what);
}

function isoDate(v: unknown, what: string): string {
  const d = str(v, what);
  return /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : fail(what);
}

function optStr(v: unknown, what: string): string | null {
  return v === null || v === undefined ? null : str(v, what);
}

function optNum(v: unknown, what: string): number | null {
  return v === null || v === undefined ? null : num(v, what);
}

// --- Kite session ---------------------------------------------------------------------------

export type KiteState = "connected" | "expired" | "disconnected" | "not_configured";

export type KiteStatus = {
  status: KiteState;
  userId: string | null;
  expiresAt: string | null;
  hoursLeft: number | null;
  message: string | null;
};

export function parseKiteStatus(body: unknown): KiteStatus {
  const b = rec(body, "kite status");
  const status = b.status;
  if (
    status !== "connected" &&
    status !== "expired" &&
    status !== "disconnected" &&
    status !== "not_configured"
  )
    fail("kite status value");
  if (bool(b.connected, "connected") !== (status === "connected")) fail("kite connected flag");
  return {
    status,
    userId: optStr(b.user_id, "user_id"),
    expiresAt: optStr(b.expires_at, "expires_at"),
    hoursLeft: optNum(b.hours_left, "hours_left"),
    message: optStr(b.message, "message"),
  };
}

export function parseLoginUrl(body: unknown): string {
  const url = str(rec(body, "login url").login_url, "login_url");
  if (!url.startsWith("https://kite.zerodha.com/")) fail("login_url host");
  return url;
}

// --- Admin overview -------------------------------------------------------------------------

export type JobStatus = "idle" | "running" | "ok" | "failed" | "stopped";
export type JobAction = "start" | "stop" | "pause" | "resume";

export type JobRun = {
  name: string;
  status: JobStatus;
  startedAt: string | null;
  finishedAt: string | null;
  error: string | null;
  inserted: number | null;
  quarantined: number | null;
  symbolErrors: number | null;
  retryError: string | null;
  retries: number | null;
};

export type ScheduledJob = { id: string; nextRunAt: string | null; trigger: string };
export type Count = { timeframe: string; label: string; count: number };
export type CursorError = {
  grain: "1d" | "1m";
  symbol: string;
  error: string | null;
  updatedAt: string;
};

export type AdminOverview = {
  generatedAt: string;
  historySince: string;
  jobRunning: boolean;
  scheduler: { running: boolean; jobs: ScheduledJob[] } | null;
  runs: JobRun[];
  operableJobs: string[] | null;
  referenceErrors: { dataset: string; date: string; error: string | null }[];
  quarantine: Count[];
  flags: Count[];
  cursorErrors: CursorError[];
};

function parseRun(v: unknown): JobRun {
  const r = rec(v, "run");
  const status = r.status;
  if (
    status !== "idle" &&
    status !== "running" &&
    status !== "ok" &&
    status !== "failed" &&
    status !== "stopped"
  )
    fail("run status");
  const result = isRecord(r.result) ? r.result : {};
  // daily → result.backfill; intraday → result.intraday; NSE archive → result.nse; repair →
  // result.repair; analysis → result.adjustments; any job while running → result.progress
  const totals =
    [
      result.backfill,
      result.intraday,
      result.nse,
      result.repair,
      result.adjustments,
      result.reference,
      result.fo,
      result.progress,
    ].find(isRecord) ?? null;
  return {
    name: str(r.name, "run name"),
    status,
    startedAt: optStr(r.started_at, "started_at"),
    finishedAt: optStr(r.finished_at, "finished_at"),
    error: optStr(r.error, "error"),
    inserted: optNum(totals?.inserted, "inserted"),
    quarantined: optNum(totals?.quarantined, "quarantined"),
    symbolErrors: optNum(totals?.errors, "errors"),
    retryError: optStr(result.retry_error, "retry error"),
    retries: optNum(result.resumes, "retries"),
  };
}

function parseCounts(v: unknown, key: "reason" | "flag"): Count[] {
  return arr(v, key).map((x) => {
    const c = rec(x, key);
    return {
      timeframe: str(c.timeframe, "timeframe"),
      label: str(c[key], key),
      count: num(c.count, "count"),
    };
  });
}

export function parseOverview(body: unknown): AdminOverview {
  const b = rec(body, "overview");
  const sched = b.scheduler === null ? null : rec(b.scheduler, "scheduler");
  return {
    generatedAt: str(b.generated_at, "generated_at"),
    historySince: str(b.history_since, "history_since"),
    jobRunning: bool(b.job_running, "job_running"),
    scheduler: sched && {
      running: bool(sched.running, "scheduler running"),
      jobs: arr(sched.jobs, "scheduler jobs").map((x) => {
        const j = rec(x, "scheduled job");
        return {
          id: str(j.id, "job id"),
          nextRunAt: optStr(j.next_run_at, "next_run_at"),
          trigger: str(j.trigger, "trigger"),
        };
      }),
    },
    runs: arr(b.runs, "runs").map(parseRun),
    operableJobs:
      b.operable_jobs === undefined
        ? null
        : arr(b.operable_jobs, "operable jobs").map((id) => str(id, "operable job")),
    referenceErrors: arr(b.reference_errors ?? [], "reference errors").map((item) => {
      const e = rec(item, "reference error");
      return {
        dataset: str(e.dataset, "reference dataset"),
        date: isoDate(e.date, "reference file date"),
        error: optStr(e.error, "reference error detail"),
      };
    }),
    quarantine: parseCounts(b.quarantine, "reason"),
    flags: parseCounts(b.flags, "flag"),
    cursorErrors: arr(b.cursor_errors, "cursor_errors").map((x) => {
      const e = rec(x, "cursor error");
      const grain = e.grain;
      if (grain !== "1d" && grain !== "1m") fail("cursor grain");
      return {
        grain,
        symbol: str(e.symbol, "symbol"),
        error: optStr(e.error, "error"),
        updatedAt: str(e.updated_at, "updated_at"),
      };
    }),
  };
}

export async function controlJob(id: string, action: JobAction, token: string, startedAt?: string) {
  const query = startedAt ? `?${new URLSearchParams({ started_at: startedAt })}` : "";
  const res = await fetch(`/api/admin/jobs/${encodeURIComponent(id)}/${action}${query}`, {
    method: "POST",
    cache: "no-store",
    redirect: "error",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null);
    if (res.status === 401) throw new Error("Admin token rejected.");
    throw new Error(
      isRecord(body) && typeof body.detail === "string"
        ? body.detail
        : `Job control failed (HTTP ${res.status}).`,
    );
  }
}

// --- 1-minute completeness ------------------------------------------------------------------

export type IntradaySymbol = {
  symbol: string;
  first: string;
  last: string;
  completePct: number;
  short: number;
  excess: number;
  missingSessions: number;
  /** NSE didn't trade the equity (suspension): reported, not a data gap */
  notTradedSessions: number;
  review: number;
};

export type IntradayCoverage = {
  generatedAt: string;
  dbSizeGb: number;
  /** DEC-2015: intraday analysis starts here by default; older 1m is stored, opt-in only */
  analysisStart: string;
  symbols: number;
  minuteBars: number;
  sessions: number;
  complete: number;
  short: number;
  excess: number;
  unknown: number;
  marketShort: number;
  missingSessions: number;
  /** NSE didn't trade the equity (suspension): reported, not a data gap */
  notTradedSessions: number;
  missingMinutes: number;
  oldest: string | null;
  newest: string | null;
  /** symbols needing the most review, worst first */
  worst: IntradaySymbol[];
};

export function parseIntradayCoverage(body: unknown, worstN = 8): IntradayCoverage {
  const b = rec(body, "intraday coverage");
  const s = rec(b.summary, "intraday summary");
  const rows = arr(b.symbols, "intraday symbols").map((x): IntradaySymbol => {
    const r = rec(x, "intraday symbol");
    return {
      symbol: str(r.symbol, "symbol"),
      first: str(r.first, "first"),
      last: str(r.last, "last"),
      completePct: num(r.complete_pct, "complete_pct"),
      short: num(r.short, "short"),
      excess: num(r.excess, "excess"),
      missingSessions: num(r.missing_sessions, "missing_sessions"),
      notTradedSessions: num(r.not_traded_sessions, "not_traded_sessions"),
      review: num(r.review, "review"),
    };
  });
  return {
    generatedAt: str(b.generated_at, "generated_at"),
    dbSizeGb: num(b.db_size_gb, "db_size_gb"),
    analysisStart: isoDate(b.analysis_start, "analysis_start"),
    symbols: num(s.symbols, "symbols"),
    minuteBars: num(s.minute_bars, "minute_bars"),
    sessions: num(s.sessions, "sessions"),
    complete: num(s.complete_sessions, "complete_sessions"),
    short: num(s.short_sessions, "short_sessions"),
    excess: num(s.excess_sessions, "excess_sessions"),
    unknown: num(s.unknown_sessions, "unknown_sessions"),
    marketShort: num(s.market_short_sessions, "market_short_sessions"),
    missingSessions: num(s.missing_sessions, "missing_sessions"),
    notTradedSessions: num(s.not_traded_sessions, "not_traded_sessions"),
    missingMinutes: num(s.missing_minutes, "missing_minutes"),
    oldest: optStr(s.oldest, "oldest"),
    newest: optStr(s.newest, "newest"),
    worst: rows
      .filter((r) => r.review > 0)
      .sort((a, b) => b.review - a.review || a.symbol.localeCompare(b.symbol))
      .slice(0, worstN),
  };
}

// --- fetchers (same-origin; Next rewrites /api/* to the backend) ----------------------------

async function getJson(path: string, fetcher: typeof fetch): Promise<unknown> {
  const res = await fetcher(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} failed: HTTP ${res.status}`);
  return res.json();
}

export const fetchOverview = async (f: typeof fetch = fetch) =>
  parseOverview(await getJson("/api/admin/overview", f));
export const fetchKiteStatus = async (f: typeof fetch = fetch) =>
  parseKiteStatus(await getJson("/api/kite/status", f));
export const fetchLoginUrl = async (f: typeof fetch = fetch) =>
  parseLoginUrl(await getJson("/api/kite/login-url", f));
export const fetchIntradayCoverage = async (f: typeof fetch = fetch) =>
  parseIntradayCoverage(await getJson("/api/data/coverage/intraday", f));
