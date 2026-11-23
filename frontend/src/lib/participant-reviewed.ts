/** Reviewed NSE values remain strings so fractional source precision is not lost. */
export type ExactRow = {
  participant: string;
  components: Record<string, string>;
  derived_totals: { long: string; short: string };
  reported_totals: { long: string; short: string };
};
export type ReviewedFile = {
  dataset: string;
  date: string;
  quality: "reconciled_within_tolerance" | "component_totals_disagree";
  warning: string;
  source: string;
  source_url: string;
  source_sha256: string;
  tolerance_contracts: number;
  source_total_differences: Record<string, string>;
  participants: ExactRow[];
};
export type ReviewedData = {
  files: ReviewedFile[];
  gaps: { dataset: string; candidate_date: string; reason: string }[];
};

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Invalid review data");
  return value as Record<string, unknown>;
}
function text(value: unknown): string {
  if (typeof value !== "string") throw new Error("Invalid review text");
  return value;
}
function decimal(value: unknown): string {
  const v = text(value);
  if (!/^\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(v)) throw new Error("Invalid exact source value");
  return v;
}
function difference(value: unknown): string {
  const v = text(value);
  if (!/^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(v)) throw new Error("Invalid source difference");
  return v;
}
function totals(value: unknown) {
  const v = record(value);
  return { long: decimal(v.long), short: decimal(v.short) };
}
function sourceUrl(value: unknown): string {
  const url = new URL(text(value));
  if (url.protocol !== "https:" || url.hostname !== "nsearchives.nseindia.com")
    throw new Error("Unexpected NSE source URL");
  return url.toString();
}
export function parseReviewed(value: unknown): ReviewedData {
  const body = record(value);
  if (
    body.scope !== "labelled_research_only" ||
    !Array.isArray(body.files) ||
    !Array.isArray(body.gaps)
  )
    throw new Error("Invalid review response");
  const files = body.files.map((entry) => {
    const f = record(entry);
    if (f.quality !== "reconciled_within_tolerance" && f.quality !== "component_totals_disagree")
      throw new Error("Unknown review quality");
    if (!Array.isArray(f.participants) || !Number.isInteger(f.tolerance_contracts))
      throw new Error("Invalid review file");
    const quality: ReviewedFile["quality"] = f.quality;
    return {
      dataset: text(f.dataset),
      date: text(f.date),
      quality,
      warning: text(f.warning),
      source: text(f.source),
      source_url: sourceUrl(f.source_url),
      source_sha256: text(f.source_sha256),
      tolerance_contracts: f.tolerance_contracts as number,
      source_total_differences: Object.fromEntries(
        Object.entries(record(f.source_total_differences)).map(([key, value]) => [
          key,
          difference(value),
        ]),
      ),
      participants: f.participants.map((entry) => {
        const row = record(entry);
        const fields = record(row.components);
        return {
          participant: text(row.participant),
          components: Object.fromEntries(
            Object.entries(fields).map(([key, value]) => [key, decimal(value)]),
          ),
          derived_totals: totals(row.derived_totals),
          reported_totals: totals(row.reported_totals),
        };
      }),
    };
  });
  return {
    files,
    gaps: body.gaps.map((entry) => {
      const g = record(entry);
      return {
        dataset: text(g.dataset),
        candidate_date: text(g.candidate_date),
        reason: text(g.reason),
      };
    }),
  };
}
export async function fetchReviewed(): Promise<ReviewedData> {
  const res = await fetch("/api/data/participant-reviewed", {
    cache: "no-store",
    redirect: "error",
  });
  if (!res.ok) throw new Error(`Review data unavailable (HTTP ${res.status})`);
  return parseReviewed(await res.json());
}
