"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { formatDuration, formatInt, formatIstTime, formatPct } from "@/lib/format";
import { fetchCoverage, fetchJobs, type IngestJob, type IntradayProgress } from "@/lib/ingest";

const FAST_MS = 5_000;
const SLOW_MS = 30_000;

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

const STATUS_STYLE: Record<IngestJob["status"], { label: string; className: string }> = {
  idle: { label: "○ idle", className: "text-muted-foreground" },
  running: { label: "● running", className: "text-primary" },
  ok: { label: "✓ finished", className: "text-gain" },
  failed: { label: "✕ failed", className: "text-loss" },
  stopped: { label: "■ stopped", className: "text-warn" },
};

export function IngestPanel() {
  const jobs = useQuery({
    queryKey: ["ingest-jobs"],
    queryFn: () => fetchJobs(),
    refetchInterval: (q) => (q.state.data?.busy ? FAST_MS : SLOW_MS),
  });
  const busy = jobs.data?.busy ?? false;
  const coverage = useQuery({
    queryKey: ["coverage"],
    queryFn: () => fetchCoverage(),
    refetchInterval: busy ? FAST_MS : SLOW_MS,
  });
  const now = useNow(busy);

  if (jobs.isPending || coverage.isPending) {
    return <p className="text-muted-foreground">Loading data status…</p>;
  }
  if (jobs.error || coverage.error) {
    return (
      <p role="alert" className="text-loss">
        ✕ Couldn&apos;t read data status from the backend — progress unknown.
      </p>
    );
  }

  const job = jobs.data;
  const cov = coverage.data;
  const style = STATUS_STYLE[job.status];
  const pct = cov.symbols > 0 ? (100 * cov.withData) / cov.symbols : 0;
  const elapsed =
    job.startedAt === null
      ? null
      : (job.finishedAt ? Date.parse(job.finishedAt) : now) - Date.parse(job.startedAt);

  return (
    <div className="flex flex-col gap-4 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span>
          {job.name === "intraday_ingest" ? "1-minute backfill" : "Daily ingestion"}{" "}
          <span className={style.className}>{style.label}</span>
        </span>
        {job.startedAt && (
          <span className="text-muted-foreground">
            started {formatIstTime(job.startedAt)}
            {elapsed !== null && ` · ${formatDuration(elapsed)}`}
          </span>
        )}
      </div>

      {job.intraday && <IntradayProgressView p={job.intraday} />}

      <div>
        <div className="mb-1 flex justify-between text-muted-foreground">
          <span>Symbols with stored daily bars</span>
          <span>
            {formatInt(cov.withData)} / {formatInt(cov.symbols)}
            {cov.symbols === 0 && " (universe not loaded yet)"}
          </span>
        </div>
        <div
          className="h-2 overflow-hidden rounded bg-muted"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={cov.symbols}
          aria-valuenow={cov.withData}
          aria-label="Symbols with stored daily bars"
        >
          <div className="h-full bg-primary transition-[width]" style={{ width: `${pct}%` }} />
        </div>
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1">
        <dt className="text-muted-foreground">Clean daily bars</dt>
        <dd>{formatInt(cov.cleanBars)}</dd>
        <dt className="text-muted-foreground">Quarantined (unresolved)</dt>
        <dd>{formatInt(cov.quarantined)}</dd>
        <dt className="text-muted-foreground">Duplicate dates</dt>
        <dd>{formatInt(cov.duplicateDates)}</dd>
        <dt className="text-muted-foreground">≥250 clean bars</dt>
        <dd>
          {formatInt(cov.sufficient)} symbols ({formatPct(cov.sufficientPct)})
        </dd>
        {job.symbolErrors !== null && (
          <>
            <dt className="text-muted-foreground">Symbols with errors</dt>
            <dd className={job.symbolErrors > 0 ? "text-warn" : undefined}>
              {formatInt(job.symbolErrors)}
            </dd>
          </>
        )}
      </dl>

      {job.error && (
        <p role="alert" className="text-loss">
          ✕ {job.error}
        </p>
      )}

      <div className="rounded border border-border p-3">
        <p className={cov.passed ? "text-gain" : "text-warn"}>
          {cov.passed ? "▲ Coverage gate: PASS" : "▼ Coverage gate: not passed"}
          <span className="text-muted-foreground"> · as of session {cov.asOfSession}</span>
        </p>
        {!cov.passed && cov.failures.length > 0 && (
          <ul className="mt-1 list-disc pl-5 text-muted-foreground">
            {cov.failures.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function IntradayProgressView({ p }: { p: IntradayProgress }) {
  const used = p.dbSizeGb ?? 0;
  const pct = Math.min(100, (100 * used) / p.budgetGb);
  return (
    <div className="flex flex-col gap-2 rounded border border-border p-3">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1">
        <dt className="text-muted-foreground">Rounds · windows</dt>
        <dd>
          {formatInt(p.rounds)} · {formatInt(p.windows)}
        </dd>
        <dt className="text-muted-foreground">1-minute bars added (this run)</dt>
        <dd>{formatInt(p.inserted)}</dd>
        <dt className="text-muted-foreground">Every symbol back to</dt>
        <dd>{p.deepestCommonDate ?? "—"}</dd>
      </dl>
      <div>
        <div className="mb-1 flex justify-between text-muted-foreground">
          <span>Database vs budget</span>
          <span>
            {p.dbSizeGb === null ? "—" : `${p.dbSizeGb.toFixed(2)} GB`} / {p.budgetGb} GB
          </span>
        </div>
        <div
          className="h-2 overflow-hidden rounded bg-muted"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={p.budgetGb}
          aria-valuenow={used}
          aria-label="Database size versus disk budget"
        >
          <div
            className={`h-full ${pct > 90 ? "bg-warn" : "bg-gain"}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
    </div>
  );
}
