"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchOverview, type AdminOverview, type JobRun } from "@/lib/admin";
import { formatDuration, formatInt, formatIstDateTime } from "@/lib/format";

const JOB_NAMES: Record<string, string> = {
  daily_ingest: "Daily bars",
  intraday_ingest: "1-minute bars",
  ticks_retention_cleanup: "Tick retention cleanup",
  nse_archive_ingest: "NSE archive + adjustments",
  adjustment_analysis: "Adjustment analysis",
  repair_provisional: "Provisional-bar repair",
};

const STATUS: Record<JobRun["status"], { text: string; className: string }> = {
  idle: { text: "○ idle", className: "text-neutral-400" },
  running: { text: "● running", className: "text-sky-400" },
  ok: { text: "✓ ok", className: "text-emerald-400" },
  failed: { text: "✕ failed", className: "text-red-400" },
};

const QUARANTINE_REASONS: Record<string, string> = {
  non_canonical_ts: "timestamp not on the minute / day",
  non_positive_price: "zero or negative price",
  ohlc_order: "high/low out of order",
  negative_volume: "negative volume",
  out_of_range: "value too large to store",
  unparseable: "unparseable record",
  duplicate_in_batch: "same date twice in one batch",
  conflicts_with_stored: "differs from the stored bar",
  superseded_provisional: "provisional bar replaced by the final one (repair record)",
  index_gap: "index jump too large",
};

export function useOverview() {
  return useQuery({
    queryKey: ["admin-overview"],
    queryFn: () => fetchOverview(),
    refetchInterval: (q) => (q.state.data?.jobRunning ? 5_000 : 30_000),
  });
}

function name(id: string): string {
  return JOB_NAMES[id] ?? id;
}

export function JobsPanel() {
  const o = useOverview();
  if (o.isPending) return <p className="text-neutral-400">Loading jobs…</p>;
  if (o.error) {
    return (
      <p role="alert" className="text-red-400">
        ✕ Couldn&apos;t read job status from the backend — state unknown.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-5 text-sm">
      <Schedule o={o.data} />
      <Runs o={o.data} />
    </div>
  );
}

function Schedule({ o }: { o: AdminOverview }) {
  if (o.scheduler === null) {
    return <p className="text-amber-400">▼ Scheduler state unknown (not reported).</p>;
  }
  return (
    <div>
      <h3 className="mb-2 font-medium">
        Scheduled{" "}
        <span className={o.scheduler.running ? "text-emerald-400" : "text-red-400"}>
          {o.scheduler.running ? "▲ scheduler running" : "✕ scheduler stopped"}
        </span>
      </h3>
      <table className="w-full text-left">
        <thead className="text-neutral-400">
          <tr>
            <th className="py-1 font-normal">Job</th>
            <th className="py-1 font-normal">Next run</th>
          </tr>
        </thead>
        <tbody>
          {o.scheduler.jobs.map((j) => (
            <tr key={j.id} className="border-t border-neutral-800">
              <td className="py-1" title={j.trigger}>
                {name(j.id)}
              </td>
              <td className="py-1">{j.nextRunAt ? formatIstDateTime(j.nextRunAt) : "paused"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Runs({ o }: { o: AdminOverview }) {
  return (
    <div>
      <h3 className="mb-2 font-medium">
        Recent runs{" "}
        <span className="font-normal text-neutral-500">
          (since backend start, {formatIstDateTime(o.historySince)})
        </span>
      </h3>
      {o.runs.length === 0 ? (
        <p className="text-neutral-400">No job has run since the backend started.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[32rem] text-left">
            <thead className="text-neutral-400">
              <tr>
                <th className="py-1 font-normal">Job</th>
                <th className="py-1 font-normal">Status</th>
                <th className="py-1 font-normal">Started</th>
                <th className="py-1 font-normal">Took</th>
                <th className="py-1 text-right font-normal">Added</th>
                <th className="py-1 text-right font-normal">Quarantined</th>
              </tr>
            </thead>
            <tbody>
              {o.runs.map((r) => (
                <RunRow key={`${r.name}-${r.startedAt}`} r={r} now={Date.parse(o.generatedAt)} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function RunRow({ r, now }: { r: JobRun; now: number }) {
  const s = STATUS[r.status];
  const took =
    r.startedAt === null
      ? null
      : (r.finishedAt ? Date.parse(r.finishedAt) : now) - Date.parse(r.startedAt);
  return (
    <>
      <tr className="border-t border-neutral-800">
        <td className="py-1">{name(r.name)}</td>
        <td className={`py-1 ${s.className}`}>{s.text}</td>
        <td className="py-1">{r.startedAt ? formatIstDateTime(r.startedAt) : "—"}</td>
        <td className="py-1">{took === null ? "—" : formatDuration(took)}</td>
        <td className="py-1 text-right">{r.inserted === null ? "—" : formatInt(r.inserted)}</td>
        <td className="py-1 text-right">
          {r.quarantined === null ? "—" : formatInt(r.quarantined)}
        </td>
      </tr>
      {(r.error || (r.symbolErrors ?? 0) > 0) && (
        <tr>
          <td colSpan={6} className="pb-1 text-xs text-red-400">
            {r.error && `✕ ${r.error}`}
            {(r.symbolErrors ?? 0) > 0 && ` · ${formatInt(r.symbolErrors ?? 0)} symbols errored`}
          </td>
        </tr>
      )}
    </>
  );
}

export function QualityPanel() {
  const o = useOverview();
  if (o.isPending) return <p className="text-neutral-400">Loading data quality…</p>;
  if (o.error) {
    return (
      <p role="alert" className="text-red-400">
        ✕ Couldn&apos;t read data quality from the backend — state unknown.
      </p>
    );
  }
  const { quarantine, flags, cursorErrors } = o.data;
  return (
    <div className="flex flex-col gap-5 text-sm">
      <div>
        <h3 className="mb-2 font-medium">
          Quarantined rows, all time (kept for audit, never used as data — includes ones later
          resolved)
        </h3>
        {quarantine.length === 0 ? (
          <p className="text-neutral-400">None.</p>
        ) : (
          <table className="w-full text-left">
            <tbody>
              {quarantine.map((q) => (
                <tr key={`${q.timeframe}-${q.label}`} className="border-t border-neutral-800">
                  <td className="w-12 py-1 text-neutral-400">{q.timeframe}</td>
                  <td className="py-1">{QUARANTINE_REASONS[q.label] ?? q.label}</td>
                  <td className="py-1 text-right">{formatInt(q.count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div>
        <h3 className="mb-2 font-medium">Flagged for review (stored, but worth a look)</h3>
        {flags.length === 0 ? (
          <p className="text-neutral-400">None.</p>
        ) : (
          <table className="w-full text-left">
            <tbody>
              {flags.map((f) => (
                <tr key={`${f.timeframe}-${f.label}`} className="border-t border-neutral-800">
                  <td className="w-12 py-1 text-neutral-400">{f.timeframe}</td>
                  <td className="py-1">{f.label.replaceAll("_", " ")}</td>
                  <td className="py-1 text-right">{formatInt(f.count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div>
        <h3 className="mb-2 font-medium">Symbols whose last fetch failed</h3>
        {cursorErrors.length === 0 ? (
          <p className="text-emerald-400">✓ None.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {cursorErrors.map((e) => (
              <li key={`${e.grain}-${e.symbol}`} className="text-amber-400">
                {e.symbol} <span className="text-neutral-400">({e.grain})</span> — {e.error ?? "?"}
                <span className="text-neutral-500"> · {formatIstDateTime(e.updatedAt)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
