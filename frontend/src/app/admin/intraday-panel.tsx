"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchIntradayCoverage } from "@/lib/admin";
import { formatInt, formatPct } from "@/lib/format";

export function IntradayPanel() {
  const cov = useQuery({
    queryKey: ["intraday-coverage"],
    queryFn: () => fetchIntradayCoverage(),
    refetchInterval: 60_000,
  });
  if (cov.isPending) return <p className="text-neutral-400">Loading 1-minute coverage…</p>;
  if (cov.error) {
    return (
      <p role="alert" className="text-red-400">
        ✕ Couldn&apos;t read 1-minute coverage from the backend — state unknown.
      </p>
    );
  }
  const c = cov.data;
  const judged = c.sessions - c.unknown - c.marketShort;
  return (
    <div className="flex flex-col gap-4 text-sm">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1">
        <dt className="text-neutral-400">Bars stored</dt>
        <dd>
          {formatInt(c.minuteBars)} across {formatInt(c.symbols)} symbols
        </dd>
        <dt className="text-neutral-400">Range</dt>
        <dd>
          {c.oldest ?? "—"} → {c.newest ?? "—"}
        </dd>
        <dt className="text-neutral-400">Analysis starts</dt>
        <dd>
          {c.analysisStart}{" "}
          <span className="text-neutral-500">
            (default for intraday backtests and statistics; older 1m stays stored and chartable,
            opt-in only, badged &ldquo;feed gaps&rdquo;)
          </span>
        </dd>
        <dt className="text-neutral-400">Database size</dt>
        <dd>{c.dbSizeGb.toFixed(2)} GB</dd>
        <dt className="text-neutral-400">Complete sessions</dt>
        <dd>
          {formatInt(c.complete)} / {formatInt(judged)}
          {judged > 0 && ` (${formatPct((100 * c.complete) / judged)})`}
        </dd>
        <dt className="text-neutral-400">Short · excess · missing</dt>
        <dd className="text-amber-400">
          {formatInt(c.short)} · {formatInt(c.excess)} · {formatInt(c.missingSessions)}
        </dd>
        <dt className="text-neutral-400">Not traded (NSE)</dt>
        <dd>
          {formatInt(c.notTradedSessions)}{" "}
          <span className="text-neutral-500">
            (suspensions per NSE&apos;s archive — not data gaps)
          </span>
        </dd>
        <dt className="text-neutral-400">Market-wide short days</dt>
        <dd>
          {formatInt(c.marketShort)}{" "}
          <span className="text-neutral-500">(outage / muhurat — reported, not flagged)</span>
        </dd>
        <dt className="text-neutral-400">No norm yet (unknown)</dt>
        <dd>{formatInt(c.unknown)}</dd>
      </dl>
      <p className="text-neutral-500">
        Each session is judged against that instrument&apos;s own normal length (re-baselined when
        it changes for good, e.g. the 2026 closing auction: 375 → 360 minutes).
      </p>
      {c.worst.length > 0 && (
        <div className="overflow-x-auto">
          <h3 className="mb-2 font-medium">Most sessions to review</h3>
          <table className="w-full min-w-[28rem] text-left">
            <thead className="text-neutral-400">
              <tr>
                <th className="py-1 font-normal">Symbol</th>
                <th className="py-1 font-normal">From</th>
                <th className="py-1 text-right font-normal">Complete</th>
                <th className="py-1 text-right font-normal">Short</th>
                <th className="py-1 text-right font-normal">Excess</th>
                <th className="py-1 text-right font-normal">Missing</th>
              </tr>
            </thead>
            <tbody>
              {c.worst.map((s) => (
                <tr key={s.symbol} className="border-t border-neutral-800">
                  <td className="py-1">{s.symbol}</td>
                  <td className="py-1">{s.first}</td>
                  <td className="py-1 text-right">{formatPct(s.completePct)}</td>
                  <td className="py-1 text-right">{formatInt(s.short)}</td>
                  <td className="py-1 text-right">{formatInt(s.excess)}</td>
                  <td className="py-1 text-right">{formatInt(s.missingSessions)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
