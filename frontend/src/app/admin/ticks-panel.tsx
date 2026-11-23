"use client";

import { useQuery } from "@tanstack/react-query";

import { formatInt, formatIstDateTime, formatPrice } from "@/lib/format";
import { fetchTicks } from "@/lib/ticks";

const RUN_TEXT: Record<string, string> = {
  ok: "✓ ok",
  failed: "✕ failed",
  running: "● running",
  no_session: "○ no Kite login",
};

/** Slice 1d: live ticks, the Parquet export ledger, and option-chain snapshots. */
export function TicksPanel() {
  const q = useQuery({
    queryKey: ["ticks-status"],
    queryFn: () => fetchTicks(),
    refetchInterval: 15_000,
  });
  if (q.isPending) return <p className="text-muted-foreground">Loading live-data status…</p>;
  if (q.error) {
    return (
      <p role="alert" className="text-loss">
        ✕ Couldn&apos;t read the live-data status — state unknown.
      </p>
    );
  }
  const t = q.data;
  const c = t.capture;
  return (
    <div className="flex flex-col gap-4 text-sm">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1">
        <dt className="text-muted-foreground">Live ticks today</dt>
        <dd>
          {c === null ? (
            <span className="text-muted-foreground">Not set up (needs Kite keys)</span>
          ) : (
            <>
              {c.connected ? (
                <span className="text-gain">▲ connected</span>
              ) : (
                <span>{RUN_TEXT[c.status] ?? c.status}</span>
              )}{" "}
              · {formatInt(c.ticks)} ticks · {formatInt(c.instruments)} instruments ·{" "}
              {formatInt(c.reconnects)} reconnects
              {c.rejected + c.unknownTokens > 0 && (
                <span className="text-warn">
                  {" "}
                  · {formatInt(c.rejected + c.unknownTokens)} refused
                </span>
              )}
              {c.dropped > 0 && (
                <span className="text-loss">
                  {" "}
                  · {formatInt(c.dropped)} lost (database unreachable)
                </span>
              )}
              {c.detail && <span className="text-muted-foreground"> · {c.detail}</span>}
            </>
          )}
        </dd>
        <dt className="text-muted-foreground">Ticks in the database</dt>
        <dd>
          about {formatInt(t.hot.rowsEstimate)}
          {t.hot.oldest && ` (since ${t.hot.oldest.slice(0, 10)})`}
        </dd>
        <dt className="text-muted-foreground">Saved to Parquet files</dt>
        <dd>
          {formatInt(t.exports.verified)} days checked and saved · {formatInt(t.exports.rows)} ticks
          · {(t.exports.bytes / 1024 ** 3).toFixed(2)} GB
          {t.exports.mismatch + t.exports.error > 0 && (
            <span className="text-loss">
              {" "}
              · ✕ {formatInt(t.exports.mismatch + t.exports.error)} days NOT saved (kept in the
              database)
            </span>
          )}
        </dd>
        <dt className="text-muted-foreground">Option chains</dt>
        <dd>
          {formatInt(t.chain.snapshotsToday)} snapshots today (one every 5 minutes, 09:15–15:30)
        </dd>
      </dl>
      {t.chain.latest.length > 0 && (
        <table className="w-full max-w-xl text-left">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 font-normal">Underlying</th>
              <th className="py-1 font-normal">Last snapshot</th>
              <th className="py-1 text-right font-normal">Spot</th>
              <th className="py-1 text-right font-normal">Contracts</th>
            </tr>
          </thead>
          <tbody>
            {t.chain.latest.map((l) => (
              <tr key={l.underlying} className="border-t">
                <td className="py-1">{l.underlying}</td>
                <td className={`py-1 ${l.status === "ok" ? "" : "text-warn"}`}>
                  {formatIstDateTime(l.snapshotTs)}
                  {l.status !== "ok" && ` (${l.status})`}
                </td>
                <td className="py-1 text-right tabular-nums">
                  {l.spotPaisa === null ? "—" : formatPrice(l.spotPaisa)}
                </td>
                <td className="py-1 text-right tabular-nums">{formatInt(l.contracts)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {t.runs.length > 0 && (
        <table className="w-full min-w-[32rem] text-left">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 font-normal">Capture day</th>
              <th className="py-1 font-normal">Result</th>
              <th className="py-1 text-right font-normal">Ticks</th>
              <th className="py-1 text-right font-normal">Refused</th>
              <th className="py-1 text-right font-normal">Reconnects</th>
            </tr>
          </thead>
          <tbody>
            {t.runs.map((r) => (
              <tr key={`${r.sessionDate}-${r.ticks}`} className="border-t">
                <td className="py-1">{r.sessionDate}</td>
                <td className="py-1">{RUN_TEXT[r.status] ?? r.status}</td>
                <td className="py-1 text-right tabular-nums">{formatInt(r.ticks)}</td>
                <td className="py-1 text-right tabular-nums">{formatInt(r.rejected)}</td>
                <td className="py-1 text-right tabular-nums">{formatInt(r.reconnects)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="text-xs text-muted-foreground">
        Ticks are kept in the database for a few days, then written to a Parquet file, read back and
        compared; a day is removed from the database only if the file matches exactly.
      </p>
    </div>
  );
}
