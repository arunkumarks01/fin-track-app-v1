/** Slice 1d contract (GET /api/data/ticks): live capture, Parquet exports, chain snapshots. */

export class TicksContractError extends Error {}

function fail(what: string): never {
  throw new TicksContractError(`unexpected ticks response: ${what}`);
}
function rec(v: unknown, what: string): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : fail(what);
}
function int(v: unknown, what: string): number {
  return Number.isInteger(v) && (v as number) >= 0 ? (v as number) : fail(what);
}
function str(v: unknown, what: string): string {
  return typeof v === "string" ? v : fail(what);
}
function optStr(v: unknown, what: string): string | null {
  return v === null || v === undefined ? null : str(v, what);
}

export type Capture = {
  status: string;
  connected: boolean;
  ticks: number;
  rejected: number;
  dropped: number;
  unknownTokens: number;
  reconnects: number;
  buffered: number;
  instruments: number;
  lastTickAt: string | null;
  detail: string | null;
};

export type CaptureRun = {
  sessionDate: string;
  status: string;
  instruments: number;
  ticks: number;
  rejected: number;
  reconnects: number;
  detail: string | null;
};

export type ChainLatest = {
  underlying: string;
  snapshotTs: string;
  status: string;
  contracts: number;
  spotPaisa: number | null;
};

export type TicksStatus = {
  capture: Capture | null;
  runs: CaptureRun[];
  hot: { rowsEstimate: number; oldest: string | null; newest: string | null };
  exports: {
    verified: number;
    dropped: number;
    mismatch: number;
    error: number;
    rows: number;
    bytes: number;
    newest: string | null;
  };
  chain: { snapshotsToday: number; latest: ChainLatest[] };
};

function capture(v: unknown): Capture | null {
  if (v === null) return null;
  const c = rec(v, "capture");
  return {
    status: str(c.status, "status"),
    connected: c.connected === true,
    ticks: int(c.ticks, "ticks"),
    rejected: int(c.rejected, "rejected"),
    dropped: int(c.dropped, "dropped"),
    unknownTokens: int(c.unknown_tokens, "unknown_tokens"),
    reconnects: int(c.reconnects, "reconnects"),
    buffered: int(c.buffered, "buffered"),
    instruments: int(c.instruments, "instruments"),
    lastTickAt: optStr(c.last_tick_at, "last_tick_at"),
    detail: optStr(c.detail, "detail"),
  };
}

export function parseTicks(body: unknown): TicksStatus {
  const b = rec(body, "body");
  if (!Array.isArray(b.runs)) fail("runs");
  const hot = rec(b.hot, "hot");
  const ex = rec(b.exports, "exports");
  const ch = rec(b.chain, "chain");
  if (!Array.isArray(ch.latest)) fail("chain.latest");
  return {
    capture: capture(b.capture),
    runs: b.runs.map((x) => {
      const r = rec(x, "run");
      return {
        sessionDate: str(r.session_date, "session_date"),
        status: str(r.status, "run status"),
        instruments: int(r.instruments, "instruments"),
        ticks: int(r.ticks, "run ticks"),
        rejected: int(r.rejected, "run rejected"),
        reconnects: int(r.reconnects, "run reconnects"),
        detail: optStr(r.detail, "run detail"),
      };
    }),
    hot: {
      rowsEstimate: int(hot.rows_estimate, "rows_estimate"),
      oldest: optStr(hot.oldest, "hot.oldest"),
      newest: optStr(hot.newest, "hot.newest"),
    },
    exports: {
      verified: int(ex.verified, "verified"),
      dropped: int(ex.dropped, "dropped"),
      mismatch: int(ex.mismatch, "mismatch"),
      error: int(ex.error, "error"),
      rows: int(ex.rows, "export rows"),
      bytes: int(ex.bytes, "bytes"),
      newest: optStr(ex.newest, "export newest"),
    },
    chain: {
      snapshotsToday: int(ch.snapshots_today, "snapshots_today"),
      latest: ch.latest.map((x) => {
        const c = rec(x, "chain latest");
        return {
          underlying: str(c.underlying, "underlying"),
          snapshotTs: str(c.snapshot_ts, "snapshot_ts"),
          status: str(c.status, "chain status"),
          contracts: int(c.contracts, "contracts"),
          spotPaisa: c.spot_paisa === null ? null : int(c.spot_paisa, "spot_paisa"),
        };
      }),
    },
  };
}

export async function fetchTicks(f: typeof fetch = fetch): Promise<TicksStatus> {
  const res = await f("/api/data/ticks", { cache: "no-store" });
  if (!res.ok) throw new Error(`ticks request failed: HTTP ${res.status}`);
  return parseTicks(await res.json());
}
