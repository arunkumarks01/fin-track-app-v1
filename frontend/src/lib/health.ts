/**
 * Backend /health contract. Parsed strictly: an unexpected shape is an error, never a
 * guessed "ok" (honest surfaces — law 2; fail closed — law 9).
 */
export type HealthStatus = {
  status: "ok" | "degraded";
  paperMode: true;
  db: "ok" | "error";
  scheduler: "running" | "stopped";
  version: string;
};

export class HealthContractError extends Error {}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function parseHealth(body: unknown): HealthStatus {
  if (!isRecord(body)) throw new HealthContractError("health: not an object");
  const { status, paper_mode, db, scheduler, version } = body;
  if (status !== "ok" && status !== "degraded") throw new HealthContractError("health: status");
  if (paper_mode !== true) throw new HealthContractError("health: paper_mode must be true");
  if (db !== "ok" && db !== "error") throw new HealthContractError("health: db");
  if (scheduler !== "running" && scheduler !== "stopped")
    throw new HealthContractError("health: scheduler");
  if (typeof version !== "string") throw new HealthContractError("health: version");
  return { status, paperMode: true, db, scheduler, version };
}

/** Same-origin call; Next.js rewrites /api/* to the backend (next.config.ts). */
export async function fetchHealth(fetcher: typeof fetch = fetch): Promise<HealthStatus> {
  const res = await fetcher("/api/health", { cache: "no-store" });
  // 503 still carries a valid degraded body — parse it rather than hide it.
  if (!res.ok && res.status !== 503) {
    throw new Error(`health request failed: HTTP ${res.status}`);
  }
  return parseHealth(await res.json());
}
