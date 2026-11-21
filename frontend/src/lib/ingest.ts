/**
 * Ingestion job + coverage-gate contracts (backend /api/data/*). Parsed strictly: an
 * unexpected shape is an error, never a guessed value (honest surfaces — law 2).
 */
export class IngestContractError extends Error {}

type Rec = Record<string, unknown>;

function isRecord(v: unknown): v is Rec {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function fail(what: string): never {
  throw new IngestContractError(`ingest contract: ${what}`);
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

export type JobStatus = "idle" | "running" | "ok" | "failed";

export type IntradayProgress = {
  rounds: number;
  windows: number;
  inserted: number;
  deepestCommonDate: string | null;
  dbSizeGb: number | null;
  budgetGb: number;
};

function parseIntraday(v: unknown): IntradayProgress | null {
  if (!isRecord(v)) return null;
  return {
    rounds: num(v.rounds, "rounds"),
    windows: num(v.windows, "windows"),
    inserted: num(v.inserted, "inserted"),
    deepestCommonDate: optStr(v.deepest_common_date, "deepest_common_date"),
    dbSizeGb:
      v.db_size_gb === null || v.db_size_gb === undefined ? null : num(v.db_size_gb, "db_size_gb"),
    budgetGb: num(v.budget_gb, "budget_gb"),
  };
}

export type IngestJob = {
  busy: boolean;
  name: string;
  status: JobStatus;
  startedAt: string | null;
  finishedAt: string | null;
  error: string | null;
  inserted: number | null;
  quarantined: number | null;
  symbolErrors: number | null;
  intraday: IntradayProgress | null;
};

export function parseJobs(body: unknown): IngestJob {
  if (!isRecord(body) || typeof body.busy !== "boolean" || !isRecord(body.last)) fail("jobs");
  const last = body.last;
  const status = last.status;
  if (status !== "idle" && status !== "running" && status !== "ok" && status !== "failed")
    fail("status");
  const result = isRecord(last.result) ? last.result : {};
  const backfill = isRecord(result.backfill) ? result.backfill : null;
  return {
    busy: body.busy,
    name: str(last.name, "name"),
    status,
    startedAt: optStr(last.started_at, "started_at"),
    finishedAt: optStr(last.finished_at, "finished_at"),
    error: optStr(last.error, "error"),
    inserted: backfill ? num(backfill.inserted, "inserted") : null,
    quarantined: backfill ? num(backfill.quarantined, "quarantined") : null,
    symbolErrors: backfill ? num(backfill.errors, "errors") : null,
    // live `progress` belongs to whichever job runs; only the 1-minute job's has this shape
    intraday: parseIntraday(
      result.intraday ?? (last.name === "intraday_ingest" ? result.progress : undefined),
    ),
  };
}

export type Coverage = {
  passed: boolean;
  asOfSession: string;
  failures: string[];
  symbols: number;
  withData: number;
  sufficient: number;
  sufficientPct: number;
  cleanBars: number;
  quarantined: number;
  duplicateDates: number;
};

export function parseCoverage(body: unknown): Coverage {
  if (!isRecord(body) || typeof body.passed !== "boolean") fail("coverage");
  if (!isRecord(body.summary) || !Array.isArray(body.symbols) || !Array.isArray(body.failures))
    fail("coverage shape");
  const s = body.summary;
  const withData = body.symbols.filter(
    (row) => isRecord(row) && typeof row.clean_bars === "number" && row.clean_bars > 0,
  ).length;
  return {
    passed: body.passed,
    asOfSession: str(body.as_of_session, "as_of_session"),
    failures: body.failures.map((f) => str(f, "failure")),
    symbols: num(s.symbols, "symbols"),
    withData,
    sufficient: num(s.sufficient, "sufficient"),
    sufficientPct: num(s.sufficient_pct, "sufficient_pct"),
    cleanBars: num(s.clean_bars, "clean_bars"),
    quarantined: num(s.quarantined, "quarantined"),
    duplicateDates: num(s.duplicate_dates, "duplicate_dates"),
  };
}

async function getJson(path: string, fetcher: typeof fetch): Promise<unknown> {
  const res = await fetcher(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} failed: HTTP ${res.status}`);
  return res.json();
}

export async function fetchJobs(fetcher: typeof fetch = fetch): Promise<IngestJob> {
  return parseJobs(await getJson("/api/data/jobs", fetcher));
}

export async function fetchCoverage(fetcher: typeof fetch = fetch): Promise<Coverage> {
  return parseCoverage(await getJson("/api/data/coverage", fetcher));
}
