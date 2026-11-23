/** Slice 1f contract (GET /api/data/fo): coverage of the F&O history. Parsed strictly. */

export class FoContractError extends Error {}

function fail(what: string): never {
  throw new FoContractError(`unexpected F&O response: ${what}`);
}
function int(v: unknown, what: string): number {
  return Number.isInteger(v) && (v as number) >= 0 ? (v as number) : fail(what);
}
function optDate(v: unknown, what: string): string | null {
  if (v === null) return null;
  return typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : fail(what);
}

export const FO_KIND_LABEL: Record<string, string> = {
  IDF: "index futures",
  STF: "stock futures",
  IDO: "index options",
  STO: "stock options",
  IVF: "VIX futures",
};

export type FoSummary = {
  daysOk: number;
  daysMissing: number;
  daysError: number;
  oldest: string | null;
  newest: string | null;
  rows: number;
  underlyings: number;
  rowsByKind: Record<string, number>;
  tableGb: number;
};

export function parseFo(body: unknown): FoSummary {
  if (typeof body !== "object" || body === null) fail("body");
  const b = body as Record<string, unknown>;
  const k = b.rows_by_kind;
  if (typeof k !== "object" || k === null || Array.isArray(k)) fail("rows_by_kind");
  if (typeof b.table_gb !== "number" || b.table_gb < 0) fail("table_gb");
  return {
    daysOk: int(b.days_ok, "days_ok"),
    daysMissing: int(b.days_missing, "days_missing"),
    daysError: int(b.days_error, "days_error"),
    oldest: optDate(b.oldest, "oldest"),
    newest: optDate(b.newest, "newest"),
    rows: int(b.rows, "rows"),
    underlyings: int(b.underlyings, "underlyings"),
    rowsByKind: Object.fromEntries(
      Object.entries(k).map(([key, n]) => [key, int(n, `rows_by_kind.${key}`)]),
    ),
    tableGb: b.table_gb,
  };
}

export async function fetchFo(f: typeof fetch = fetch): Promise<FoSummary> {
  const res = await f("/api/data/fo", { cache: "no-store" });
  if (!res.ok) throw new Error(`F&O request failed: HTTP ${res.status}`);
  return parseFo(await res.json());
}
