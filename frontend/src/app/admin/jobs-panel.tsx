"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  controlJob,
  fetchOverview,
  type AdminOverview,
  type JobAction,
  type JobRun,
} from "@/lib/admin";
import { formatDuration, formatInt, formatIstDateTime } from "@/lib/format";
import { label, QUARANTINE_REASONS } from "@/lib/labels";

const JOB_NAMES: Record<string, string> = {
  daily_ingest: "Daily bars",
  intraday_ingest: "1-minute bars",
  ticks_retention_cleanup: "Tick export to Parquet + cleanup",
  ticks_capture: "Live tick capture",
  option_chain: "Option-chain snapshot",
  nse_archive_ingest: "NSE archive + adjustments",
  adjustment_analysis: "Adjustment analysis",
  repair_provisional: "Provisional-bar repair",
  fo_history_ingest: "Futures & options history (NSE)",
  reference_ingest: "Delivery % + F&O positions (NSE)",
  news_fetch: "News headlines",
  fred_fetch: "FRED series",
  scoring_swing: "Swing strategy scan",
  scoring_intraday: "Intraday strategy scan",
};

const STATUS: Record<JobRun["status"], { text: string; className: string }> = {
  idle: { text: "○ idle", className: "text-muted-foreground" },
  running: { text: "● running", className: "text-primary" },
  ok: { text: "✓ ok", className: "text-gain" },
  failed: { text: "✕ failed", className: "text-loss" },
  stopped: { text: "■ stopped", className: "text-warn" },
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
  if (o.isPending) return <p className="text-muted-foreground">Loading jobs…</p>;
  if (o.error) {
    return (
      <p role="alert" className="text-loss">
        ✕ Couldn&apos;t read job status from the backend — state unknown.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-5 text-sm">
      <JobControls
        o={o.data}
        refresh={async () => {
          await o.refetch();
        }}
      />
      <Schedule o={o.data} />
      <Runs o={o.data} />
      {o.data.referenceErrors.length > 0 && (
        <details>
          <summary className="cursor-pointer font-medium">Recent reference-file errors</summary>
          <p className="mt-2 text-xs text-muted-foreground">
            Archived participant files remain excluded from clean data. Their exact values and
            source-total differences are available in the{" "}
            <a className="text-primary underline" href="/api/admin/participant-review">
              participant review report
            </a>
            .
          </p>
          <ul className="mt-2 space-y-1 text-xs text-loss">
            {o.data.referenceErrors.map((e) => (
              <li key={`${e.dataset}-${e.date}`}>
                {e.date} · {e.dataset} · {e.error ?? "No error detail recorded"}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function JobControls({ o, refresh }: { o: AdminOverview; refresh: () => Promise<void> }) {
  const [selected, setSelected] = useState("");
  const [token, setToken] = useState("");
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<{ text: string; error: boolean } | null>(null);
  const jobs = o.operableJobs;
  if (!jobs?.length)
    return <p className="text-muted-foreground">Job controls need the updated backend.</p>;
  const id = jobs.includes(selected) ? selected : jobs[0]!;
  const schedule = o.scheduler?.jobs.find((job) => job.id === id);
  const running = o.runs.find((run) => run.name === id && run.status === "running");

  async function submit(action: JobAction) {
    setPending(true);
    setResult(null);
    try {
      await controlJob(
        id,
        action,
        token.trim(),
        action === "stop" ? (running?.startedAt ?? undefined) : undefined,
      );
      setResult({
        text: {
          start: "Start requested.",
          stop: "Stop requested. The current transaction will finish rolling back before the run is marked stopped.",
          pause: "Schedule paused.",
          resume: "Schedule resumed.",
        }[action],
        error: false,
      });
      setToken("");
      await refresh();
    } catch (error) {
      setResult({
        text: error instanceof Error ? error.message : "Job control failed.",
        error: true,
      });
    } finally {
      setPending(false);
    }
  }

  const locked = pending || !token.trim();
  const fieldClass = "rounded-md border border-border bg-background px-2 py-1.5";
  return (
    <div className="space-y-2">
      <h3 className="font-medium">Job controls</h3>
      <div className="flex flex-wrap items-end gap-2">
        <label className="grid gap-1">
          Job or service
          <select
            className={fieldClass}
            value={id}
            onChange={(e) => {
              setSelected(e.target.value);
              setResult(null);
            }}
            disabled={pending}
          >
            {jobs.map((job) => (
              <option key={job} value={job}>
                {name(job)}
              </option>
            ))}
          </select>
        </label>
        <label className="grid gap-1">
          Admin API token
          <input
            type="password"
            autoComplete="off"
            className={fieldClass}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            disabled={pending}
          />
        </label>
        <Button size="sm" disabled={locked || !!running} onClick={() => void submit("start")}>
          Start now
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={locked || !running?.startedAt}
          onClick={() => void submit("stop")}
        >
          Stop run
        </Button>
        {schedule && (
          <Button
            size="sm"
            variant="outline"
            disabled={locked}
            onClick={() => void submit(schedule.nextRunAt ? "pause" : "resume")}
          >
            {schedule.nextRunAt ? "Pause schedule" : "Resume schedule"}
          </Button>
        )}
      </div>
      {schedule && (
        <p className="text-xs text-muted-foreground">
          Pausing a schedule prevents future automatic runs until resumed or the backend restarts.
          Use Stop run for work in progress.
        </p>
      )}
      {result && (
        <p
          role={result.error ? "alert" : "status"}
          className={result.error ? "text-loss" : "text-muted-foreground"}
        >
          {result.text}
        </p>
      )}
    </div>
  );
}

function Schedule({ o }: { o: AdminOverview }) {
  if (o.scheduler === null) {
    return <p className="text-warn">▼ Scheduler state unknown (not reported).</p>;
  }
  return (
    <div>
      <h3 className="mb-2 font-medium">
        Scheduled{" "}
        <span className={o.scheduler.running ? "text-gain" : "text-loss"}>
          {o.scheduler.running ? "▲ scheduler running" : "✕ scheduler stopped"}
        </span>
      </h3>
      <table className="w-full text-left">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-1 font-normal">Job</th>
            <th className="py-1 font-normal">Next run</th>
          </tr>
        </thead>
        <tbody>
          {o.scheduler.jobs.map((j) => (
            <tr key={j.id} className="border-t border-border">
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
        <span className="font-normal text-muted-foreground">
          (since backend start, {formatIstDateTime(o.historySince)})
        </span>
      </h3>
      {o.runs.length === 0 ? (
        <p className="text-muted-foreground">No job has run since the backend started.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[32rem] text-left">
            <thead className="text-muted-foreground">
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
      <tr className="border-t border-border">
        <td className="py-1">{name(r.name)}</td>
        <td className={`py-1 ${s.className}`}>{s.text}</td>
        <td className="py-1">{r.startedAt ? formatIstDateTime(r.startedAt) : "—"}</td>
        <td className="py-1">{took === null ? "—" : formatDuration(took)}</td>
        <td className="py-1 text-right">{r.inserted === null ? "—" : formatInt(r.inserted)}</td>
        <td className="py-1 text-right">
          {r.quarantined === null ? "—" : formatInt(r.quarantined)}
        </td>
      </tr>
      {(r.error || r.retryError || (r.symbolErrors ?? 0) > 0) && (
        <tr>
          <td colSpan={6} className="pb-1 text-xs text-loss">
            {r.error && `✕ ${r.error}`}
            {r.retryError && ` · ${r.retryError} (${formatInt(r.retries ?? 0)} retries)`}
            {(r.symbolErrors ?? 0) > 0 && ` · ${formatInt(r.symbolErrors ?? 0)} errors`}
          </td>
        </tr>
      )}
    </>
  );
}

export function QualityPanel() {
  const o = useOverview();
  if (o.isPending) return <p className="text-muted-foreground">Loading data quality…</p>;
  if (o.error) {
    return (
      <p role="alert" className="text-loss">
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
          <p className="text-muted-foreground">None.</p>
        ) : (
          <table className="w-full text-left">
            <tbody>
              {quarantine.map((q) => (
                <tr key={`${q.timeframe}-${q.label}`} className="border-t border-border">
                  <td className="w-12 py-1 text-muted-foreground">{q.timeframe}</td>
                  <td className="py-1">{label(QUARANTINE_REASONS, q.label)}</td>
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
          <p className="text-muted-foreground">None.</p>
        ) : (
          <table className="w-full text-left">
            <tbody>
              {flags.map((f) => (
                <tr key={`${f.timeframe}-${f.label}`} className="border-t border-border">
                  <td className="w-12 py-1 text-muted-foreground">{f.timeframe}</td>
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
          <p className="text-gain">✓ None.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {cursorErrors.map((e) => (
              <li key={`${e.grain}-${e.symbol}`} className="text-warn">
                {e.symbol} <span className="text-muted-foreground">({e.grain})</span> —{" "}
                {e.error ?? "?"}
                <span className="text-muted-foreground"> · {formatIstDateTime(e.updatedAt)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
